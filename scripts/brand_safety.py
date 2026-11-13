#!/usr/bin/env python3
"""CutSkills · 品牌安全与第三方字样检测与处置引擎。

商用片里出现别人的品牌、公司名、商标或手机号，等于替别人打广告或带来侵权风险。
本模块提供：
  1. BRAND_PATTERN 扩展词库（含 FESTOOL、RO 150、PPG、大师漆及常见建材/设备/商标）；
  2. scan_third_party_risks() 统一接口，供 check_timeline.py 与 check_edl.py 复用；
  3. 处置与核销闭环：若在 decisions.md 中写了**结构化核销行**（或在元素中显式标注
     third_party_action），自动从 warning 核销并降级为已确认说明，杜绝死锁报黄。

T-036（核销只认显式行）：decisions.md 是给人读的散文，判据不许读语感。原实现拿
AFFIRMATIVE_PATTERN 在整行里搜「保留/允许/认可」，于是 T-035 自己写的一句
「index.json 没有重写（不在本单允许改动的两个路径里）」配上同一行的 `1072`
就把 #1 的品牌告警静默核销掉了（unresolved 11→10）。现在：
  - 只有 `- [brand] #1 保留：<理由>` / `- [brand] #1 模糊：<理由>` / `- [brand] #1 裁掉`
    这一类结构化行才算核销意见（目标可以是段号，也可以是素材名）；
  - 散文一律不核销，「允许/可以/同意/认可」这类泛许可词不再单独构成核销；
  - 看起来像在说第三方字样、却没写成结构化行的句子，由 ack_hints() 打一条 warning
    提示「这句话没有被当作核销意见」。
"""
from __future__ import annotations

import re
from pathlib import Path

# 扩展品牌与第三方字样识别正则（大小写不敏感）
BRAND_PATTERN = re.compile(
    r"FESTOOL|RO\s*150|费斯托|PPG|大师漆|东鹏|MYLANDS|麦兰德|立邦|三棵树|德高|圣戈班|马贝|百得|"
    r"格力|GREE|别克|苹果|iPhone|华为|大疆|门窗|EOD|年年机电|手机号?|电话|联系方式|"
    r"logo|标志|品牌字样|第三方|打码|水印|商标",
    re.IGNORECASE
)

# ---- 核销意见只认结构化行（T-036）
# 行首必须是列表项 + `[brand]` 标签，紧跟**唯一**一个目标（段号 #1 / 第1段 / S01，或素材名/素材号）：
#   - [brand] #1 保留：客户授权露出
#   - [brand] #1 模糊：招牌虚化
#   - [brand] #1 裁掉
#   - [brand] 1072 保留：本店自己的膜
STRUCTURED_ACK_LINE = re.compile(
    r"^\s*[-*+]\s*\[\s*(?:brand|品牌|第三方|third[-_ ]?party)\s*\]\s*(?P<rest>\S.*)$",
    re.IGNORECASE
)

# 处置词：必须显式表态「留」还是「处理掉」。
KEEP_PATTERN = re.compile(r"保留出镜|保留|无需避开|不用避开|\bkeep\b", re.IGNORECASE)
DROP_PATTERN = re.compile(
    r"裁掉|裁切|打码|模糊|遮罩|换段|\bcrop\b|\bblur\b|\bmask\b|\bcovered\b",
    re.IGNORECASE
)
# 「不保留 / 不能保留」里的「保留」是反话：算到「处理掉」这一侧。
# 同一行两边都出现 = 没拍板，不核销。
NEGATED_KEEP_PATTERN = re.compile(r"(?:不|没|未|无需|不用|不得|不许|不能|严禁|禁止|别)\s*保留")

# 结构化行里的未决弱语义：还没拍板的不算核销。
WEAK_PATTERN = re.compile(r"待确认|未确认|尚未确认|暂未确认|待定|再议|待核实|未核实|待商榷|先保留.*待")

# 段号目标（#1 / 第1段 / 第1格 / S01）
SEG_TARGET_PATTERN = re.compile(r"^(?:#(\d+)|第(\d+)[段格]|[Ss](\d+))$")

# 标题行（`# decisions` / `## 第三方字样`）要跳过；但 `#1 段落…` 这种以段号开头的正文不能跳过。
HEADING_PATTERN = re.compile(r"^#{1,6}\s")

# 散文里「像在说第三方字样」的三件套：品牌泛指词 + 表态词 + 具体目标（段号或素材名/编号）。
# 命中只用来打提示，绝不拿来核销。
PROSE_BRAND_WORD = re.compile(r"第三方|品牌|字样|商标|logo|brand", re.IGNORECASE)
PROSE_ACK_WORD = re.compile(
    r"无需避开|不用避开|不避开|保留|允许|可以|同意|认可|放行|合规|没问题|打码|裁掉|裁切|模糊|遮罩",
    re.IGNORECASE
)
# 具体目标：段号 / 素材名 / 素材号。素材号要求 4 位且不像年份（19xx·20xx），
# 否则「2026-11-11 22:22 | 方案确认 | 认可」这种日志行会被当成「在说某一段」。
PROSE_ASSET_TOKEN = r"(?<!\d)(?!19\d{2}|20\d{2})\d{4}(?!\d)"
PROSE_TARGET = re.compile(rf"#\d+|第\d+[段格]|{PROSE_ASSET_TOKEN}|[A-Za-z]{{2,}}[_-][A-Za-z0-9_-]{{4,}}")
# 只说「允许/可以/同意/保留」但带段号、不涉品牌词的行，同样算「像在拍板」——也要提醒。
PROSE_PERMISSION = re.compile(
    r"允许|可以|同意|放行|没问题|不用改|无需避开|保留|认可", re.IGNORECASE
)
# 只说「允许/可以/同意/保留」的行，要指向某一段或某支素材，才算「像在拍板」。
PROSE_SEG_TARGET = re.compile(rf"#\d+|第\d+[段格]|{PROSE_ASSET_TOKEN}")

# 泛指词集合：绝对不能作为品牌特征单独用于核销判断。
# T-036 后核销不再按散文/flag 子串匹配（只认精确指向的结构化行），此表保留给
# 散文提示（ack_hints）与后续判据作「这词本身不构成品牌」的参考。
GENERIC_BRAND_WORDS = {
    "第三方", "品牌", "字样", "商标", "标志", "打码", "水印", "logo",
    "工具", "入镜", "机身", "衣服", "贴纸", "特写", "后排", "白t", "印有",
    "区域", "画面", "镜头", "素材", "片段"
}


def structured_ack(ln: str, seg_n: int, asset_name: str) -> tuple[str, str] | None:
    """把一行 decisions.md 解析成核销意见；不是结构化行 / 没拍到板 / 指向别的段 → None。

    返回 (处置词, 整行) —— 处置词是「保留」或「裁掉」这一类显式表态。
    """
    m = STRUCTURED_ACK_LINE.match(ln)
    if not m:
        return None
    rest = m.group("rest").strip()
    # 目标 = 紧跟标签的第一个 token（#1 / 第1段 / S01 / 素材名或素材号）
    target = re.split(r"\s+", rest, maxsplit=1)[0].rstrip("：:，,。.;；|")
    if not target:
        return None

    # 一行只许指向一个段号：写了两个以上（#1 … #2）＝没说清是哪一段，不核销
    segs = {x for tup in re.findall(r"#(\d+)|第(\d+)[段格]|[Ss](\d{2})", rest) for x in tup if x}
    if len(segs) > 1:
        return None

    # 目标对得上当前段吗？
    hit = False
    sm = SEG_TARGET_PATTERN.match(target)
    if sm:
        hit = int(next(g for g in sm.groups() if g)) == int(seg_n)
    elif asset_name:
        t = target.lower()
        aid = str(asset_name).lower()
        stem = Path(str(asset_name)).stem.lower()
        hit = len(t) >= 3 and (t == aid or t == stem or t in aid or aid in t)
    if not hit:
        return None

    # 未决弱语义（「先保留，待确认」）不算拍板
    if WEAK_PATTERN.search(rest):
        return None

    keep_n = len(KEEP_PATTERN.findall(rest)) - len(NEGATED_KEEP_PATTERN.findall(rest))
    drop_n = len(DROP_PATTERN.findall(rest)) + len(NEGATED_KEEP_PATTERN.findall(rest))
    if keep_n > 0 and drop_n > 0:
        return None      # 同一行既说留又说处理掉 = 没拍板（「保留…不保留」也落到这里）
    if keep_n > 0:
        return "保留", ln.strip()
    if drop_n > 0:
        return "裁掉", ln.strip()
    return None          # 结构化行但没写处置词 —— 也不核销


def is_brand_acknowledged(seg_n: int, flags: list[str], asset_name: str,
                          decisions_text: str | None) -> tuple[bool, str]:
    """检查该段的第三方字样是否已在 decisions.md 里有**显式核销意见**。

    T-036 口径：只认结构化行，散文不读语感。
      1. 必须写成 `- [brand] #1 保留：<理由>` / `- [brand] #1 模糊：<理由>` / `- [brand] #1 裁掉`
         这一类行（见 STRUCTURED_ACK_LINE），目标可以是段号也可以是素材名；
      2. 散文里的「允许/可以/同意/认可」等泛许可词不再单独构成核销；
      3. 一行只能指向一个段号，指向别的段绝不跨段核销（T-007 打回项①）；
      4. 未决弱语义（待确认/待定/再议）不算核销；同一行既说留又说处理掉也不算。
    flags 只在文档里说明命中来源，不参与匹配——结构化行已经精确指名了目标。
    """
    if not decisions_text:
        return False, ""

    for ln in decisions_text.splitlines():
        ln_s = ln.strip()
        if not ln_s or HEADING_PATTERN.match(ln_s):
            continue
        got = structured_ack(ln_s, seg_n, asset_name)
        if not got:
            continue
        dispo, line = got
        why = "无需避开" if dispo == "保留" else "已处置"
        return True, f"decisions.md 结构化核销（{why}）：{line[:80]}"
    return False, ""


def ack_hints(decisions_text: str | None) -> list[str]:
    """散文里「像在说第三方字样 / 像在拍板」却没写成结构化行的句子 —— 打提示，绝不核销。

    两路判据：
      A. 品牌泛指词（第三方/品牌/字样/商标）+ 表态词（保留/允许/无需避开/打码…）+ 具体目标（#1/1072…）；
      B. 泛许可词（允许/可以/同意/认可…）+ 段号（#1 / 第1段）—— 如「#1 这个不用改，允许留着」。
    """
    if not decisions_text:
        return []
    out: list[str] = []
    for i, ln in enumerate(decisions_text.splitlines(), 1):
        ln_s = ln.strip()
        if not ln_s or HEADING_PATTERN.match(ln_s) or STRUCTURED_ACK_LINE.match(ln_s):
            continue
        looks_a = (PROSE_BRAND_WORD.search(ln_s) and PROSE_ACK_WORD.search(ln_s)
                   and PROSE_TARGET.search(ln_s))
        looks_b = PROSE_PERMISSION.search(ln_s) and PROSE_SEG_TARGET.search(ln_s)
        if not (looks_a or looks_b):
            continue
        snippet = ln_s if len(ln_s) <= 60 else ln_s[:57] + "…"
        out.append(f"第 {i} 行「{snippet}」")
    return out


def scan_third_party_risks(items: list[dict], index: dict, decisions_text: str | None = None,
                           hints: list[str] | None = None) -> tuple[list[dict], list[dict]]:
    """扫描片段列表中的第三方字样风险。

    参数:
      items: 片段列表，每个包含 n, asset, in, out，可带有 third_party_action 等
      index: 素材库 index.json 索引字典
      decisions_text: 项目 decisions.md 全文文本（可选）
      hints: 传入 list 时，把「看起来像在说第三方字样、但没写成结构化行」的散文行
             追加进去（只作提示，绝不核销）。本片压根没有品牌命中时不追加。

    返回:
      (unresolved, resolved)
      - unresolved: 尚未处置/未确认的风险项（需触发 warning）
      - resolved: 已在 decisions.md 的**结构化行**确认，或元素已标注处置的项（已核销）
    """
    unresolved: list[dict] = []
    resolved: list[dict] = []

    for item in items:
        asset_id = item.get("asset")
        if not asset_id:
            continue
        rec = index.get(asset_id) or {}
        t_in = float(item.get("in", 0.0))
        t_out = float(item.get("out", 0.0))
        n = item.get("n", 0)

        # 遍历素材中的 segments/l2 寻找与当前片段有重叠的 flags
        hit_flags: list[str] = []
        for g in rec.get("segments") or rec.get("l2") or []:
            g_in = float(g.get("in", 0.0))
            g_out = float(g.get("out", 0.0))
            if g_out <= t_in + 0.05 or g_in >= t_out - 0.05:
                continue
            flags = g.get("flags") or []
            matched = [str(f) for f in flags if BRAND_PATTERN.search(str(f))]
            for m in matched:
                if m not in hit_flags:
                    hit_flags.append(m)

        if not hit_flags:
            continue

        risk_entry = {
            "n": n,
            "asset": asset_id,
            "in": t_in,
            "out": t_out,
            "flags": hit_flags,
            "tag": rec.get("tag") or Path(asset_id).stem,
            "action": item.get("third_party_action") or item.get("third_party")
        }

        # 检查是否已在元素中显式处置
        act = risk_entry["action"]
        if act in ("crop", "blur", "acknowledged", "keep", "covered", "裁切", "打码", "已核实"):
            risk_entry["resolved_reason"] = f"元素显式标注处置：{act}"
            resolved.append(risk_entry)
            continue

        # 检查是否已在 decisions.md 中确认
        acked, reason = is_brand_acknowledged(n, hit_flags, asset_id, decisions_text)
        if acked:
            risk_entry["resolved_reason"] = reason
            resolved.append(risk_entry)
        else:
            unresolved.append(risk_entry)

    # 本片真有品牌命中（不论有没有核销）时，才提示那些「像在说、其实不算」的散文句——
    # 没有品牌风险可核销的片子，这句话提醒不动任何东西，只会变成黄噪声。
    if hints is not None and (unresolved or resolved):
        hints.extend(ack_hints(decisions_text))

    return unresolved, resolved
