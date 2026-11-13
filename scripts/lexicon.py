#!/usr/bin/env python3
"""CutSkills · 转写语义纠错（词表层）。

ASR 只保证「听起来像」，不保证「写对了」。品牌名、专业术语、人名，同一个音在字上
可能有四五个候选，本地模型会挑错——柒造这一库里，「瑞加」被写成「瑞家/瑞佳」5 次、
一次都没对；「石灰基」30 次全写成「石灰机」。

纠错不能靠模型自己猜，要靠背景：品牌语料手册、历史修正、客户给的标准写法。
流程是三步，职责分开：

  1. `index_assets.py --stage lint`  → 出 `_cutskills/transcript-fix.md`（脚本只统计，不下判断）
  2. 人/大模型读语料 + 上下文，把决定写进 `_cutskills/lexicon.json`（判断在这里）
  3. `index_assets.py --stage rephrase` → 按词表重算句子（不重跑 ASR，秒级）

词表是库级资产，跟着库走，跨项目复用；新建项目不用重新纠一遍。

守卫（T-017）：写进 `terms` 不等于能直接落地。**等长且不碰否定字**的替换自动应用；
**不等长**的必须条目自己标 `unsafe: true` 才不会被当成“没做过判断”，且调用方还得显式
允许；**增删否定字**（不/没/别/未/唔/冇）的一律硬拦。被拦下的条目不会静默消失，
`python3 scripts/lexicon.py --library <库>` 把它们连原因一起列出来。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SENTINEL = "\x00"   # ASR 句界哨兵，不进字幕
DATA = Path(__file__).resolve().parent / "data"      # 判据数据（词表、量级表）都放这里
# 单字拖音：ASR 把它们当真字，字幕上就成了「诶两百平嘅厨房啊」「我哋嘅诶四大轩王」。
# 只收**纯粹是停顿声**的几个（诶欸哎呃嗯）；「啊呀哦噢喔」是句尾语气词，
# 说话人真的在用它收句，删掉字幕反而断了语气。
NOISE_CHARS = set("诶欸哎呃嗯")
NOISE_PHRASES = ("喺喺",)   # 连着念两次同一个介词的卡顿

# 否定字。换掉一个否定字，整句话的意思就反了，观众听到的和字幕写的是两件事——
# 这比错别字严重得多（准绳 A5：严禁静默反转句意），所以单独一条硬闸。
NEG_CHARS = frozenset("不没别別未唔冇")

# 句尾叠字（口吃重尾）。ASR 会把拖音录成同字重复：「讲下下」「坐坐坐」。
STUTTER_RUN = 3                                  # 连念三次以上，几乎只可能是口吃
LEGIT_RUN = frozenset("哈呵嘿嘻哗对好系係得行")    # 「哈哈哈」「对对对」「好好好」是正常说法，不是口吃
# 这些字在句尾**不叠音成词**：撞上就说明是拖音。反过来「慢慢」「坐坐」「睇睇」是正常
# 叠词（粤语里「坐坐」＝坐一会儿），一律不动——宁可漏判，不许把好句子改坏。
STUTTER_TAIL = frozenset("下咗嘅啦呀咧喎啵嘛啲哋咁先就系喺有都")
_TRAIL_PUNCT = "，。！？、；：…—～,.!?;:」』）) \t"


def strip_noise(text: str) -> str:
    """从**字幕文本**里剔掉拖音。只动字幕：真值（词级时间戳）一个字不改，
    否则 evidence_guard 会判「字幕上出现的字音频里没说过」。
    """
    t = str(text or "")
    for ph in NOISE_PHRASES:
        t = t.replace(ph, ph[0])
    return "".join(ch for ch in t if ch not in NOISE_CHARS)


def load_suspects(path: Path | None = None) -> list[dict]:
    """读 `data/suspect-words.txt`：同音残词与口吃重尾的**嫌疑**表。

    格式（制表符分隔，`#` 开头为注释）：
        错词 <TAB> 建议词 <TAB> 等长|不等长 <TAB> 备注 [<TAB> 排除后接]

    为什么是数据文件而不是代码里的常量：这张表是行业与库的经验（「生万」在海鲜店是
    「生猛」，在别的片子可能不是），改它的人不该被要求改 Python、重跑一遍测试；
    lint_transcript 出工单、check_subs 上屏拦人，读的都是这同一份。
    为什么每条都要标等长：等长替换不动字级时间戳，可以直接落地；不等长要均摊字宽，
    时间轴会跟着漂，默认得人工点头（见 `guard()`）。
    备注里出现「待人工」＝这条只是怀疑：脚本可以命中它，但不能替人拍板。
    """
    p = Path(path) if path else DATA / "suspect-words.txt"
    out: list[dict] = []
    if not p.exists():
        return out
    for ln in p.read_text(encoding="utf-8").splitlines():
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        cols = ln.split("\t")
        if len(cols) < 3:
            continue                      # 少一列就别猜，宁可漏（坏行会在 lint 里显形）
        bad, good, kind = cols[0].strip(), cols[1].strip(), cols[2].strip()
        note = cols[3].strip() if len(cols) > 3 else ""
        # 第五列可选：错词后面紧跟这些字就不算命中（「效果好**好**」是正常说法）
        exclude = [x for x in (cols[4].split("|") if len(cols) > 4 else []) if x]
        if not bad or not good:
            continue
        out.append({"bad": bad, "good": good,
                    "same_len": kind != "不等长" and len(bad) == len(good),
                    "kind": kind, "note": note, "exclude": exclude,
                    "needs_human": "待人工" in note})
    return out


def stutter_tail(text: str) -> str | None:
    """句尾叠字（口吃重尾）：返回叠出来的那一截（「下下」），没有就 None。

    为什么判据写在 lexicon 里：lint_transcript（出待审工单）和 check_subs（上屏门禁）
    都要判同一件事，判断只能有一份，不然两边会各说各话。
    为什么不复用 speech_hygiene 的判据：那套吃的是「词级时间戳 + 静音实测」（它要量停顿、
    量接缝），字幕这一层手上只有一句话的文字，时间戳已经并进 cue 了；能复用的部分已经
    复用——`NOISE_PHRASES` 里的「喺喺」这类卡顿照旧算口吃。
    """
    t = str(text or "").rstrip(_TRAIL_PUNCT)
    n = len(t)
    if n < 2:
        return None
    # 句尾是单字拖音（诶欸哎呃嗯）不算口吃重尾：那是 `strip_noise()` 的活，
    # 上屏前已经被剔掉了。两边都报一遍只会让工单变噪音。
    if t[-1] in NOISE_CHARS:
        return None
    k = 1
    while k < n and t[n - 1 - k] == t[n - 1]:
        k += 1
    # ① 句尾连念 ≥3 次同一个字：「坐坐坐」「唔唔唔」
    if k >= STUTTER_RUN and t[-1] not in LEGIT_RUN:
        return t[n - k:]
    # ② 句尾叠了两个不能再叠的虚字：「讲下下」「好嘅嘅」。
    #    只认虚字表，是因为「慢慢」「坐坐」「睇睇」同样是叠字、却是正常说法。
    if k == 2 and t[-1] in STUTTER_TAIL:
        return t[-2:]
    # ③ 已知卡顿短语（复用上面那份 NOISE_PHRASES）
    for ph in NOISE_PHRASES:
        if t.endswith(ph):
            return ph
    return None


def suspect_hits(text: str, suspects: list[dict] | None = None) -> list[dict]:
    """在一段文本里找嫌疑词（同音残词）。返回命中列表，`at` 是字符偏移。

    两个细节是拿真实库调出来的，不是理论上该有：
      · **排除后接**：同一库里有「你要效果好好啊得噶」这种真话，「效果好」在这里是
        正常说法（效果好+好）。表里第五列写了「好|正|得」，命中后先看一眼后接字。
      · **跨条命中靠调用方拼**：字幕被宽度拆开时，「生」和「万鱼池」会落在两条 cue
        里，逐条扫永远扫不到「生万」。要抓这类，调用方得先把被宽度拆断的相邻 cue
        拼回去（check_subs.semantic_judge 就是这么干的）。
    """
    table = suspects if suspects is not None else load_suspects()
    t = str(text or "")
    out: list[dict] = []
    for s in table:
        bad = s["bad"]
        i = t.find(bad)
        while i >= 0:
            tail = t[i + len(bad):]
            if not any(tail.startswith(x) for x in s.get("exclude") or []):
                out.append({**s, "at": i, "text": t})
            i = t.find(bad, i + 1)
    out.sort(key=lambda h: h["at"])
    return out


def _term_value(v) -> tuple[str, dict]:
    """词表条目归一：老写法（`"错写": "正确写法"`）与带守卫标记的新写法都能读。

    为什么要允许 dict：`{"to": "十几米", "unsafe": true}` 这种条目自己带着
    「我做过判断、我知道这是不等长」的签名；纯字符串条目没有签名，于是不安全的那一类
    会被守卫拒绝——这正是「不等长必须显式标记」的落点。老库不用改一个字节。
    """
    if isinstance(v, dict):
        return str(v.get("to") or v.get("good") or ""), v
    return str(v or ""), {}


def _neg_delta(bad: str, good: str) -> str:
    """替换前后否定字各出现几次。有差就返回人读得懂的一句话，没有就空串。"""
    out = []
    for ch in sorted(NEG_CHARS):
        a, b = bad.count(ch), good.count(ch)
        if a != b:
            out.append(f"「{ch}」{a}→{b}")
    return "、".join(out)


def plan(bad: str, good: str, meta: dict | None = None, *, human: bool = False,
         allow_unsafe: bool = False, allow_negation_edit: bool = False) -> dict:
    """这一条替换该怎么处理。返回 `{"apply": bool, "why": str, "info": str}`。

    判「谁写的」比判「等不等长」更要紧——准绳 A5 第 2 条防的是**机器自作主张**，不是防人：

      · `human=True`：人写进 `<库>/_cutskills/lexicon.json` 的条目。写下去那一次就是
        显式批准，不等长照旧应用；只在报告里以 info 点名（字宽均摊、时间戳按新字数
        重分布）。把这类一并拦下是改错了方向：那是已经上过片的修正，静默失效比错字更难查
        （2026-11-13 复核实证：柒造 naming 片的「物为不叫造为→物，为什么不叫造漆」被拦后退回原样，
        golden 也抓不到，因为 golden 验的是已渲成片、不重算字幕）。
      · `human=False`：工具扫出来的建议（`scripts/data/suspect-words.txt` 这一路）。
        机器没资格替人拍板：不等长必须条目自己标 `unsafe: true`，且调用方再显式允许
        （`--allow-unsafe`）才落地。

    两条路一样：**增删否定字（不/没/别/未/唔/冇）一律硬拦**，人写的也不放过——
    它会把句意静默反转，只有读过上下文、显式 `--allow-negation-edit` 才放。
    """
    meta = meta or {}
    if not bad or not good or bad == good:
        return {"apply": False, "why": "错写与建议相同（空替换）", "info": ""}
    neg = _neg_delta(bad, good)
    if neg and not allow_negation_edit:
        return {"apply": False, "info": "",
                "why": (f"会增删否定字（{neg}）——句子意思会被静默反转，"
                        "必须人读上下文后显式 --allow-negation-edit")}
    if len(bad) == len(good):
        return {"apply": True, "why": "", "info": ""}
    long_note = (f"不等长（{len(bad)}→{len(good)} 字）：字宽会被均摊、"
                 "时间戳按新字数重分布")
    if human:
        return {"apply": True, "why": "",
                "info": long_note + "——库词表条目由人写定，视为已批准"}
    if not meta.get("unsafe"):
        return {"apply": False, "info": "",
                "why": long_note + "——机器建议且未标 unsafe，默认拒绝应用"}
    if not allow_unsafe:
        return {"apply": False, "info": "",
                "why": long_note + "——已标 unsafe，但调用方没有显式允许（--allow-unsafe）"}
    return {"apply": True, "why": "", "info": long_note}


def guard(bad: str, good: str, meta: dict | None = None, *,
          allow_unsafe: bool = False, allow_negation_edit: bool = False) -> tuple[bool, str]:
    """**机器建议**这一路过闸：返回 `(放行?, 拦下的原因)`。

    只给「脚本自己扫出来的候选」用（`suspect-words.txt` / `--term`）。库词表不要走这里，
    它走 `plan(..., human=True)`：人写下去就是批准，不等长只当 info 报出来。
    """
    v = plan(bad, good, meta, human=False, allow_unsafe=allow_unsafe,
             allow_negation_edit=allow_negation_edit)
    return v["apply"], v["why"]


def term_entries(lex: dict, *, allow_unsafe: bool = False,
                 allow_negation_edit: bool = False) -> tuple[list[tuple[str, str]], list[dict]]:
    """把**库词表** `terms` 归一成 `[(错写, 正确写法)]`，顺手报出被拦的与要点名的。

    `<库>/_cutskills/lexicon.json` 是人写的（`seed_template()` 里就写着「每条都是一个人
    做过的判断」），所以走 `human=True`：不等长条目照旧应用，只以 `level: "info"` 提醒；
    增删否定字的条目仍然 `level: "block"`。

    两种都不许静默丢：`lexicon.py --library` 与 `lint_transcript.py` 会把它们列出来。
    返回值第二项是 `[{from, to, why, level, marked}]`，`level` 为 `block` 或 `info`。
    """
    ok: list[tuple[str, str]] = []
    notes: list[dict] = []
    for k, v in (lex.get("terms") or {}).items():
        good, meta = _term_value(v)
        verdict = plan(k, good, meta, human=True, allow_unsafe=allow_unsafe,
                       allow_negation_edit=allow_negation_edit)
        if verdict["apply"]:
            ok.append((k, good))
            if verdict["info"]:
                notes.append({"from": k, "to": good, "why": verdict["info"],
                              "level": "info", "marked": bool(meta.get("unsafe"))})
        else:
            notes.append({"from": k, "to": good, "why": verdict["why"],
                          "level": "block", "marked": bool(meta.get("unsafe"))})
    return ok, notes


def load(sysdir: Path) -> dict:
    p = Path(sysdir) / "lexicon.json"
    if not p.exists():
        return {"version": 1, "terms": {}, "protect": []}
    try:
        lex = json.loads(p.read_text())
    except json.JSONDecodeError:
        return {"version": 1, "terms": {}, "protect": []}
    lex.setdefault("terms", {})
    lex.setdefault("protect", [])
    return lex


def _spans(text: str, terms: list[str]) -> list[tuple[int, int]]:
    out = []
    for t in terms:
        if not t:
            continue
        i = text.find(t)
        while i >= 0:
            out.append((i, i + len(t)))
            i = text.find(t, i + 1)
    return out


def _overlaps(spans: list[tuple[int, int]], a: int, b: int) -> bool:
    return any(a < e and s < b for s, e in spans)


def replace_chars(chars: list[list], lex: dict, *, allow_unsafe: bool = False,
                  allow_negation_edit: bool = False,
                  blocked: list[dict] | None = None) -> tuple[list[list], list[dict]]:
    """在字级序列上做最长匹配替换。

    `chars` 是 `[字, 起始, 结束]` 的列表；替换后的字继承原字的时间跨度（按字数均分）。
    为什么非要拆到字：替换前后字数常常不同（「石灰机」→「石灰基」同长，
    「物为不叫造为」→「物为什么不叫造漆」就不保证），按词替换会让时间戳整体错位，
    字幕就对不上了。

    `\x00` 是 ASR 句界哨兵（subs.py 用它断呼吸组）。匹配时**当它不存在**——
    2026-11-09 实测：「我们把涂料\x00吧油漆作为原料」里那条「涂料吧油漆」跨了句界，
    哨兵把词劈成两半，词表就静默不生效，字幕上照样印着「涂料吧油漆」。
    命中区间内的哨兵一并丢掉（替换文本里已经带了该有的断句）。

    守卫（T-017）：词表条目分两路——人的判断（库词表 `lexicon.json`）照旧应用，
    不等长的只当 info 点名（字宽均摊、时间戳重分布）；机器扫出来的建议（`suspect-words.txt`）
    不等长就默认拒绝。两条路都一样：增删否定字硬拦。没落地的条目**不会静默消失**——
    传 `blocked` 这个 list 进来就能拿到（`lexicon.py --library`、`lint_transcript.py` 靠它报）。
    """
    terms, notes = term_entries(lex, allow_unsafe=allow_unsafe,
                                allow_negation_edit=allow_negation_edit)
    if blocked is not None:
        # 只把「真的没落地」的交上去；info 是「落了，但我要你看见它是不等长」
        blocked.extend(n for n in notes if n["level"] == "block")
    if not terms or not chars:
        return [list(c) for c in chars], []
    terms.sort(key=lambda kv: -len(kv[0]))
    idx = [i for i, c in enumerate(chars) if c[0] != SENTINEL]
    text = "".join(chars[i][0] for i in idx)
    protected = _spans(text, lex.get("protect") or [])

    hits: dict[int, tuple[int, str]] = {}
    i = 0
    while i < len(text):
        bad = None
        for b_, g_ in terms:
            if text.startswith(b_, i) and not _overlaps(protected, i, i + len(b_)):
                bad = (b_, g_)
                break
        if bad:
            hits[i] = (i + len(bad[0]), bad[1])
            i += len(bad[0])
        else:
            i += 1
    if not hits:
        return [list(c) for c in chars], []

    out: list[list] = []
    fixes: list[dict] = []
    p, ti = 0, 0
    while p < len(chars):
        ch = chars[p]
        if ch[0] == SENTINEL:
            out.append(list(ch))
            p += 1
            continue
        if ti in hits:
            b, good = hits[ti]
            st = chars[p][1]
            en = chars[idx[b - 1]][2]
            span = max((en - st) / max(len(good), 1), 0.01)
            for k, c_ in enumerate(good):
                out.append([c_, round(st + span * k, 3), round(st + span * (k + 1), 3)])
            fixes.append({"from": text[ti:b], "to": good, "at": st})
            while p < len(chars) and ti < b:
                if chars[p][0] != SENTINEL:
                    ti += 1
                p += 1
            continue
        out.append(list(ch))
        p += 1
        ti += 1

    return out, fixes


def apply(words: list[dict], lex: dict, **kw) -> tuple[list[dict], list[dict]]:
    """词表版 replace_chars：吃 ASR 的词列表，吐纠错后的词列表。

    `**kw` 原样转给 `replace_chars`（`allow_unsafe` / `allow_negation_edit` / `blocked`）。
    """
    chars: list[list] = []
    for w in words:
        t = w.get("t") or ""
        if not t:
            continue
        n = len(t)
        span = (float(w["end"]) - float(w["start"])) / n
        for i, ch in enumerate(t):
            chars.append([ch, round(float(w["start"]) + span * i, 3),
                          round(float(w["start"]) + span * (i + 1), 3)])
    chars, fixes = replace_chars(chars, lex, **kw)
    return [{"t": c[0], "start": c[1], "end": c[2]} for c in chars], fixes


def fix_items(items: list[tuple], lex: dict, **kw) -> list[tuple]:
    """`[(字, 起, 止), …]` → 同结构。字幕、音频块、evidence 全走这一条。

    `**kw` 转给 `replace_chars`（`allow_unsafe` / `allow_negation_edit`），
    默认全关：管线上的调用方不显式开口，危险替换就不会静默上片。
    """
    if not items:
        return items
    chars, _ = replace_chars([list(it) for it in items], lex, **kw)
    return [(c[0], c[1], c[2]) for c in chars]


def fix_text(text: str, lex: dict, **kw) -> str:
    """纯文本版（没有字级时间戳的场合，比如 evidence、看板口播原文）。"""
    if not text:
        return text
    chars = [[ch, float(i), float(i + 1)] for i, ch in enumerate(text)]
    chars, _ = replace_chars(chars, lex, **kw)
    return "".join(c[0] for c in chars)


def fix_timeline(tl: dict, lex: dict) -> int:
    """把词表（与拖音清单）落到**时间轴的 evidence 上**，返回改了几条。

    以前只有字幕走词表，evidence 还是 ASR 原字，于是同一屏上：
    字卡写「四大天王」、字幕写「四大轩王」——两份真值自己打自己。
    """
    n = 0
    for e in tl.get("elements") or []:
        ev = e.get("evidence") or ""
        if not ev:
            continue
        new = strip_noise(fix_text(ev, lex))
        if new != ev:
            e["evidence_was"] = e.get("evidence_was") or ev
            e["evidence"] = new
            n += 1
    return n


def violations(texts, lex: dict) -> list[tuple[str, str]]:
    """词表里的「错写法」还出现在哪条文本里。验收用——字面检查比人眼可靠。"""
    bad = [k for k in (lex.get("terms") or {}) if k]
    hits: list[tuple[str, str]] = []
    for t in texts:
        tt = t or ""
        for b in bad:
            if b in tt:
                hits.append((b, tt))
    return hits


def seed_template() -> dict:
    return {
        "version": 1,
        "updated": None,
        "why": "每条都是一个人做过的判断，不是机器猜的。改之前先看 transcript-fix.md 的上下文。",
        "source": [],
        "terms": {},
        "protect": [],
    }


# ---------------------------------------------------------------- 守卫自检（T-017）

def audit_library(lib: Path) -> list[dict]:
    """把库里词表逐条过一遍闸，报「默认会不会落地」。

    为什么必须先分清「谁写的」：库词表是人写的（`seed_template()` 的 why 就写着那句话），
    不等长照旧应用，只当 info 点名；只有机器建议才需要 `unsafe` 签名。
    这个入口的职责是让人一眼看到「哪条没落地、为什么」，而不是把人写的东西变成可疑对象。
    """
    lex = load(Path(lib) / "_cutskills")
    rows: list[dict] = []
    for k, v in (lex.get("terms") or {}).items():
        good, meta = _term_value(v)
        v_default = plan(k, good, meta, human=True)
        v_neg = plan(k, good, meta, human=True, allow_negation_edit=True)
        rows.append({"from": k, "to": good, "same_len": len(k) == len(good),
                     "marked": bool(meta.get("unsafe")),
                     "default": v_default["apply"], "why": v_default["why"],
                     "info": v_default["info"],
                     "with_negation": v_neg["apply"]})
    return rows


def _cli_judge(bad: str, good: str, *, allow_unsafe: bool, allow_negation_edit: bool) -> dict:
    """判一条**候选**替换（还没写进词表）能不能过闸。

    `--allow-unsafe` 在这里当成「我愿意给这条标 unsafe」：候选还没进词表，
    没有签名可读，不给它这个假设的话，这个开关就永远看不出差别。
    """
    ok, why = guard(bad, good, {"unsafe": allow_unsafe}, allow_unsafe=allow_unsafe,
                    allow_negation_edit=allow_negation_edit)
    return {"from": bad, "to": good, "same_len": len(bad) == len(good),
            "negation": _neg_delta(bad, good), "ok": ok, "why": why}


def main() -> int:
    """词表守卫的两个只读入口。

      --library <库>              把库里词表逐条过闸（人写的＝已批准，只把不等长当 info 点名）
      --term "错写=>建议词"       判一条**机器候选**替换（严格口径：不等长需 unsafe + 显式允许）

    为什么是只读：词表是人做过的判断（`seed_template()` 里写着），脚本不代写。
    """
    import argparse
    ap = argparse.ArgumentParser(description="CutSkills · 转写词表守卫自检")
    ap.add_argument("--library", default=None, help="素材库根（读 <库>/_cutskills/lexicon.json）")
    ap.add_argument("--term", default=None, help="候选替换，写法「错写=>建议词」，例「效果好=>效果图」")
    ap.add_argument("--allow-unsafe", action="store_true",
                    help="允许应用「不等长且已标 unsafe」的条目（默认拒绝）")
    ap.add_argument("--allow-negation-edit", action="store_true",
                    help="允许增删否定字（不/没/别/未/唔/冇）——高危，默认硬拦")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    out: dict = {}
    if a.library:
        rows = audit_library(Path(a.library).expanduser())
        out["terms"] = rows
        if not a.json:
            print(f"词表 {len(rows)} 条：默认放行 {sum(1 for r in rows if r['default'])} · "
                  f"被拦 {sum(1 for r in rows if not r['default'])}")
            for r in rows:
                if r["default"] and not r["info"]:
                    continue
                mark = "i" if r["default"] else "✗"
                print(f"  {mark} `{r['from']}` → `{r['to']}`：{r['info'] or r['why']}")
                if not r["default"] and r["with_negation"]:
                    print("    这条只差否定字一道闸：人读过上下文后可加 --allow-negation-edit")
    if a.term:
        bad, _, good = a.term.partition("=>")
        if not good:
            print("格式：--term \"错写=>建议词\"", file=sys.stderr)
            return 2
        j = _cli_judge(bad.strip(), good.strip(), allow_unsafe=a.allow_unsafe,
                       allow_negation_edit=a.allow_negation_edit)
        out["judge"] = j
        if not a.json:
            print(f"`{bad}` → `{good}`：{'放行' if j['ok'] else '拦下'}"
                  f"{'（' + j['why'] + '）' if j['why'] else ''}")
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
