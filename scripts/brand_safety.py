#!/usr/bin/env python3
"""CutSkills · 品牌安全与第三方字样检测与处置引擎。

商用片里出现别人的品牌、公司名、商标或手机号，等于替别人打广告或带来侵权风险。
本模块提供：
  1. BRAND_PATTERN 扩展词库（含 FESTOOL、RO 150、PPG、大师漆及常见建材/设备/商标）；
  2. scan_third_party_risks() 统一接口，供 check_timeline.py 与 check_edl.py 复用；
  3. 处置与核销闭环：若在 decisions.md 中留痕确认（或在元素中显式标注 third_party_action），
     自动从 warning 核销并降级为已确认说明，杜绝死锁报黄。
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

# 明确核销放行（白名单）：无需避开 / 真实施工工具保留 / 客户认可 / 已处理
AFFIRMATIVE_PATTERN = re.compile(
    r"无需避开|不用避开|不避开|无须避开|免避开|保留|真实工具|施工实拍|实拍出镜|客观存在|"
    r"客户认可|已确认|认可|允许|放行|合规|没问题|可以出镜|正常出镜|"
    r"已打码|已裁切|已换段|已处理|keep|allow|acknowledged",
    re.IGNORECASE
)

# 否定/要求避开指示/未决弱语义（黑名单）：
# 包含明确指示不能保留、必须打码、必须换段，以及「待确认/未确认/待定/再议」等未拍板弱语义的，坚决不予核销
NEGATIVE_PATTERN = re.compile(
    r"不能保留|不可保留|不许保留|不得保留|不要保留|禁止|严禁|不得出镜|不可出镜|"
    r"必须避开|需要避开|应当避开|必须打码|需要打码|必须换|需要换|必须裁|需要裁|不能露|不可露|"
    r"待确认|未确认|尚未确认|暂未确认|待定|再议|待核实|未核实|待商榷|先保留.*待",
    re.IGNORECASE
)

# 泛指词集合：绝对不能作为品牌特征单独用于跨段核销判断
GENERIC_BRAND_WORDS = {
    "第三方", "品牌", "字样", "商标", "标志", "打码", "水印", "logo",
    "工具", "入镜", "机身", "衣服", "贴纸", "特写", "后排", "白t", "印有",
    "区域", "画面", "镜头", "素材", "片段"
}


def is_brand_acknowledged(seg_n: int, flags: list[str], asset_name: str, decisions_text: str | None) -> tuple[bool, str]:
    """检查该段的第三方字样是否已在 decisions.md 中确认放行（例如经用户确认无需避开或已处置）。
    
    严谨性规则（T-007 第二轮整改）：
      1. 跨段防误核销：若 decisions 行显式指定了其他段号（如 #2），绝对不得跨段核销当前段（#1）；
      2. 泛指词隔离：第三方/品牌/字样/商标等泛指词不得单独作为命中品牌的依据，必须命中 flags 里实际具体品牌词；
      3. 必须包含明确的放行/确认语义（AFFIRMATIVE_PATTERN，如「无需避开」「真实工具保留」「已确认」）；
      4. 弱语义拒收：若包含否定整改或「待确认/未确认/待定/再议」等未决状态（NEGATIVE_PATTERN），坚决拒收。
    """
    if not decisions_text:
        return False, ""

    lines = decisions_text.splitlines()
    for ln in lines:
        ln_s = ln.strip()
        if not ln_s or ln_s.startswith("#"):
            continue

        # 检查是否显式提到了其他段号（如 #2, #3, 第2段等）
        other_segs = set(re.findall(r"#(\d+)|第(\d+)[段格]|S(\d{2})", ln_s))
        mentioned_segs = {int(x) for tup in other_segs for x in tup if x}
        if mentioned_segs and seg_n not in mentioned_segs:
            # 这一行显式指名了其他段号且不包含当前段，绝对不得跨段核销当前段
            continue

        # 检查段号匹配
        hit_seg = seg_n in mentioned_segs if mentioned_segs else (
            f"#{seg_n}" in ln_s or f"第{seg_n}段" in ln_s or f"第{seg_n}格" in ln_s or f"S{seg_n:02d}" in ln_s
        )

        # 检查具体品牌特征匹配（排除泛指词）
        hit_brand = False
        for f in flags:
            words = [w.strip() for w in re.split(r"[\s_（）()，,：:「」\"']+", str(f)) if len(w.strip()) >= 2]
            for w in words:
                if w.lower() in GENERIC_BRAND_WORDS:
                    continue
                if w.lower() in ln_s.lower():
                    hit_brand = True
                    break
            if hit_brand:
                break

        # 也可比对具体素材名关键标识（排除泛指词）
        if not hit_brand and asset_name:
            stem = Path(asset_name).stem
            stem_words = [w for w in re.split(r"[\s_]+", stem) if len(w) >= 3 and w.lower() not in GENERIC_BRAND_WORDS]
            for sw in stem_words:
                if sw.lower() in ln_s.lower():
                    hit_brand = True
                    break

        # 如果既没提到当前段号也没提到该段的具体品牌词，跳过
        if not (hit_seg or hit_brand):
            continue

        # 如果包含否定整改语义或未决弱语义（如「待确认」「待定」「不能保留」），坚决不予核销
        if NEGATIVE_PATTERN.search(ln_s):
            continue

        # 必须匹配明确的肯定放行词（包含用户确认无需避开/真实工具保留）
        if AFFIRMATIVE_PATTERN.search(ln_s):
            return True, f"decisions.md 确认留痕（无需避开/保留）：{ln_s[:60]}"

    return False, ""


def scan_third_party_risks(items: list[dict], index: dict, decisions_text: str | None = None) -> tuple[list[dict], list[dict]]:
    """扫描片段列表中的第三方字样风险。

    参数:
      items: 片段列表，每个包含 n, asset, in, out，可带有 third_party_action 等
      index: 素材库 index.json 索引字典
      decisions_text: 项目 decisions.md 全文文本（可选）

    返回:
      (unresolved, resolved)
      - unresolved: 尚未处置/未确认的风险项（需触发 warning）
      - resolved: 已在 decisions.md 确认或已标注处置的项（已核销）
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

    return unresolved, resolved
