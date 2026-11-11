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


def is_brand_acknowledged(seg_n: int, flags: list[str], asset_name: str, decisions_text: str | None) -> tuple[bool, str]:
    """检查该段的第三方字样是否已在 decisions.md 中拍板确认。"""
    if not decisions_text:
        return False, ""

    lines = decisions_text.splitlines()
    for ln in lines:
        ln_s = ln.strip()
        if not ln_s or ln_s.startswith("#"):
            continue
        # 关键词匹配
        if not any(k in ln_s for k in ("第三方", "品牌", "字样", "打码", "商标", "FESTOOL", "PPG", "保留", "核实", "认可", "裁切")):
            continue

        # 命中段号
        hit_seg = f"#{seg_n}" in ln_s or f"第{seg_n}段" in ln_s or f"第{seg_n}格" in ln_s or f"S{seg_n:02d}" in ln_s
        if hit_seg:
            return True, f"decisions.md 确认留痕：{ln_s[:60]}"

        # 或者命中具体素材名 / 标志名
        for f in flags:
            words = [w for w in re.split(r"[\s_（）()，,：:]+", f) if len(w) >= 2]
            for w in words:
                if w in ln_s and any(action in ln_s for action in ("保留", "认可", "确认", "处置", "核实")):
                    return True, f"decisions.md 确认留痕：{ln_s[:60]}"

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
