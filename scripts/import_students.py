"""
scripts/import_students.py —— 批量导入学生账号（幂等）
======================================================
用法（PythonAnywhere Bash）：
    cd ~/mysite && python3.10 scripts/import_students.py 学生账号清单.xlsx
    # 或不带参数，自动找当前目录下的 .xlsx

行为：
1. 读取 xlsx 第 1 个工作表，表头需含：姓名 / 账号 / 初始密码（序号列可选）；
2. 角色一律 member（普通成员）；账号已存在则跳过（不覆盖密码与昵称）；
3. 逐行打印结果，最后汇总；全部操作写入审计日志。

注意：openpyxl 为 PythonAnywhere 预装库，无需 pip install。
"""

from __future__ import annotations

import glob
import os
import sys

PROJECT_HOME: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_HOME not in sys.path:
    sys.path.insert(0, PROJECT_HOME)

import common       # noqa: E402  密码哈希
import portal_db    # noqa: E402  数据访问


def find_xlsx(argv: list[str]) -> str:
    if len(argv) > 1:
        return argv[1]
    candidates: list[str] = glob.glob(os.path.join(PROJECT_HOME, "*.xlsx")) + \
        glob.glob(os.path.join(os.getcwd(), "*.xlsx"))
    if not candidates:
        sys.exit("用法: python3.10 scripts/import_students.py <清单.xlsx>")
    return candidates[0]


def main() -> None:
    path: str = find_xlsx(sys.argv)
    print(f"读取清单: {path}")
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb.worksheets[0]

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        sys.exit("清单为空")
    header = [str(c or "").strip() for c in rows[0]]
    try:
        i_name: int = header.index("姓名")
        i_user: int = header.index("账号")
        i_pwd: int = header.index("初始密码")
    except ValueError:
        sys.exit(f"表头需包含 姓名/账号/初始密码，实际: {header}")

    member = portal_db.get_role_by_name("member")
    if member is None:
        sys.exit("member 角色不存在，请先运行 python3.10 seed_admin.py")
    role_id: int = int(member["id"])

    created: int = 0
    skipped: int = 0
    errors: int = 0
    for idx, row in enumerate(rows[1:], start=2):
        nickname: str = str(row[i_name] or "").strip()
        username: str = str(row[i_user] or "").strip()
        password: str = str(row[i_pwd] or "").strip()
        if not username:
            continue
        if not nickname or not password:
            print(f"  [错误] 第{idx}行 {username}: 姓名或密码为空")
            errors += 1
            continue
        if portal_db.get_user_by_username(username) is not None:
            print(f"  [跳过] {username:<14} {nickname}（已存在）")
            skipped += 1
            continue
        portal_db.create_user(username, nickname,
                              common.hash_password(password), role_id)
        print(f"  [新建] {username:<14} {nickname}")
        created += 1

    portal_db.add_audit(None, "import_students", os.path.basename(path),
                        f"created={created} skipped={skipped} errors={errors}")
    print(f"\n完成：新建 {created}，跳过 {skipped}，错误 {errors}")


if __name__ == "__main__":
    main()
