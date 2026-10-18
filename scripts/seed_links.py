#!/usr/bin/env python3
"""
scripts/seed_links.py —— 资源导航预置数据：6 分类 31 条友链（幂等）
=================================================================
用法（PythonAnywhere Bash）：
    cd ~/mysite && python3.10 scripts/seed_links.py

规则：
1. 幂等：按 (分类名 / 链接 URL) 去重，重复执行不产生重复数据；
2. 【人工核实】以下两个域名留占位（TODO_VERIFY），部署前务必核实填入真实域名：
   - 江苏城乡建设职业学院官网
   - 江苏省住房和城乡建设厅
   占位条目默认 is_active=1 但 URL 为占位符，核实后直接改库或在后台编辑；
3. 其余 29 条域名部署前逐一人工验证可达（脚本只做格式校验，不做网络请求）。
"""

from __future__ import annotations

import os
import sys

_SCRIPT_DIR: str = os.path.dirname(os.path.abspath(__file__))
_MYSITE_DIR: str = os.path.dirname(_SCRIPT_DIR)
if _MYSITE_DIR not in sys.path:
    sys.path.insert(0, _MYSITE_DIR)

import db          # noqa: E402
import portal_db   # noqa: E402

# 待人工核实的占位域名（部署前替换）
TODO_JSCC: str = "https://TODO-VERIFY-JSCC.example.cn"   # 江苏城乡建设职业学院
TODO_JSZJT: str = "https://TODO-VERIFY-JSZJT.example.cn"  # 江苏省住房和城乡建设厅

# (分类名, 颜色, 排序, [(名称, URL, 一句话说明), ...])
SEED: list[tuple[str, str, int, list[tuple[str, str, str]]]] = [
    ("时政要闻", "#C0392B", 1, [
        ("人民日报", "https://www.people.com.cn", "中共中央机关报，权威时政报道"),
        ("新华网", "https://www.news.cn", "新华社主办的国家通讯社网站"),
        ("央视网", "https://www.cctv.com", "中央广播电视总台官方网站"),
        ("光明网", "https://www.gmw.cn", "光明日报社主办的思想文化网站"),
        ("经济日报网", "https://www.ce.cn", "经济日报社主办的中国经济网"),
        ("求是网", "https://www.qstheory.cn", "求是杂志社主办的党的理论网站"),
        ("中国政府网", "https://www.gov.cn", "中华人民共和国中央人民政府门户网站"),
        ("中央纪委国家监委", "https://www.ccdi.gov.cn", "中央纪委国家监委官方网站"),
        ("中国共产党新闻网", "https://cpc.people.com.cn", "人民日报主办的党建网站"),
        ("中国青年报", "https://www.cyol.com", "共青团中央机关报网站"),
    ]),
    ("学习教育", "#2E86DE", 2, [
        ("教育部", "https://www.moe.gov.cn", "中华人民共和国教育部官方网站"),
        ("国家智慧教育平台", "https://www.smartedu.cn", "国家智慧教育公共服务平台"),
        ("中国大学生在线", "https://www.univs.cn", "教育部主办的大学生主题教育网站"),
        ("中国知网", "https://www.cnki.net", "中文学术文献检索平台"),
        ("学习强国", "https://www.xuexi.cn", "中宣部主管的学习平台"),
        ("江苏省教育考试院", "https://www.jseea.cn", "专转本等考试报名与查询"),
    ]),
    ("青年与组织", "#27AE60", 3, [
        ("共青团中央", "https://www.gqt.org.cn", "中国共产主义青年团中央委员会网站"),
        ("共产党员网", "https://www.12371.cn", "中央组织部主办的党员教育平台"),
    ]),
    ("就业升学", "#8E44AD", 4, [
        ("学信网", "https://www.chsi.com.cn", "学籍学历查询与验证官方网站"),
        ("国家大学生就业服务平台", "https://www.ncss.cn", "教育部大学生就业服务平台"),
    ]),
    ("行业专业·燃气城建", "#D35400", 5, [
        ("住房和城乡建设部", "https://www.mohurd.gov.cn", "住建领域政策法规与行业动态"),
        ("应急管理部", "https://www.mem.gov.cn", "安全生产与应急管理权威发布"),
        ("国家市场监督管理总局", "https://www.samr.gov.cn", "特种设备与市场监管信息"),
        ("国家发改委", "https://www.ndrc.gov.cn", "发展规划与价格政策发布"),
        ("国家能源局", "https://www.nea.gov.cn", "能源行业政策与统计信息"),
        ("中国城市燃气协会", "https://www.chinagas.org.cn", "燃气行业组织与技术交流"),
        ("全国标准信息公共服务平台", "https://std.samr.gov.cn", "查询 GB 燃气规范等国家标准"),
        ("江苏省住房和城乡建设厅", TODO_JSZJT, "【域名待核实】省住建厅政策发布"),
    ]),
    ("校园与工具", "#16A085", 6, [
        ("江苏城乡建设职业学院", TODO_JSCC, "【域名待核实】学校官网"),
        ("必应搜索", "https://cn.bing.com", "微软搜索引擎国内版"),
        ("Yandex", "https://yandex.com", "国际搜索引擎（英文资料检索）"),
    ]),
]


def main() -> None:
    db.init_db()
    portal_db.init_portal_db()
    portal_db.init_part5_db()

    total_cat: int = 0
    total_link: int = 0
    skipped: int = 0
    with db.get_db() as conn:
        for cat_name, color, sort, links in SEED:
            # 分类幂等（name 唯一）
            conn.execute(
                "INSERT INTO link_categories(name, color, sort) VALUES (?,?,?)"
                " ON CONFLICT(name) DO UPDATE SET color=excluded.color, sort=excluded.sort",
                (cat_name, color, sort))
            cat_id: int = int(conn.execute(
                "SELECT id FROM link_categories WHERE name=?",
                (cat_name,)).fetchone()[0])
            total_cat += 1
            for i, (name, url, desc) in enumerate(links):
                # 链接幂等：同分类下同 URL 已存在则跳过
                dup = conn.execute(
                    "SELECT id FROM friendly_links WHERE url=?", (url,)).fetchone()
                if dup is not None:
                    skipped += 1
                    continue
                conn.execute(
                    "INSERT INTO friendly_links(name, url, category_id, description,"
                    " sort, is_active, created_by, created_at)"
                    " VALUES (?,?,?,?,?,1,NULL,?)",
                    (name, url, cat_id, desc, i, __import__("time").time()))
                total_link += 1

    print(f"[OK] 分类 {total_cat} 个（幂等 upsert）；新增链接 {total_link} 条，"
          f"跳过已存在 {skipped} 条")
    print(f"    目标规模：6 分类 31 条（当前库内共 "
          f"{portal_db.table_counts().get('friendly_links', 0)} 条）")
    if "TODO-VERIFY" in TODO_JSCC or "TODO-VERIFY" in TODO_JSZJT:
        print("!! 部署前必做：核实并替换以下占位域名（后台 资源导航 模块可编辑）：")
        print(f"   - 江苏城乡建设职业学院  → 当前占位 {TODO_JSCC}")
        print(f"   - 江苏省住房和城乡建设厅 → 当前占位 {TODO_JSZJT}")
        print("!! 其余 29 条域名也请逐一人工验证可达")


if __name__ == "__main__":
    main()
