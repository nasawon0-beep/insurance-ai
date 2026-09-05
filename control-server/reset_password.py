#!/usr/bin/env python3
"""
비밀번호 재설정 (오프라인, 이메일 불필요).

서버 관리자가 자기 컴퓨터에서 직접 실행한다:
    python reset_password.py user@example.com 새비밀번호

가입한 이메일이 기억 안 나면 --list 로 확인:
    python reset_password.py --list
"""
import sys
from datetime import datetime, timezone

from db import connect, init
from security import hash_password


def main() -> int:
    args = sys.argv[1:]
    conn = connect()
    init(conn)

    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    if args[0] == "--list":
        rows = conn.execute(
            "SELECT email, created_at FROM users ORDER BY created_at"
        ).fetchall()
        if not rows:
            print("가입된 계정이 없습니다.")
        for r in rows:
            print(f"  {r['email']}   (가입 {r['created_at'][:10]})")
        return 0

    if len(args) != 2:
        print("사용법: python reset_password.py <이메일> <새 비밀번호>")
        return 1

    email, new_pw = args[0].strip().lower(), args[1]
    if len(new_pw) < 6:
        print("비밀번호는 6자 이상이어야 합니다.")
        return 1

    row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if not row:
        print(f"'{email}' 계정을 찾을 수 없습니다. --list 로 확인하세요.")
        return 1

    conn.execute(
        "UPDATE users SET pw_hash = ? WHERE id = ?", (hash_password(new_pw), row["id"])
    )
    conn.commit()
    print(f"완료: {email} 의 비밀번호를 재설정했습니다. ({datetime.now(timezone.utc).isoformat()[:19]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
