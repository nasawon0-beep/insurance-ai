// 로그인 화면 "이메일 기억하기" — 이메일만 저장한다. 비밀번호는 절대 저장하지 않는다.

export const REMEMBER_EMAIL_KEY = "iai.remember_email";

/** 저장된 이메일(없으면 ""). LoginScreen 의 email useState 초기값으로 쓴다. */
export function loadRememberedEmail(): string {
  try {
    return localStorage.getItem(REMEMBER_EMAIL_KEY) ?? "";
  } catch {
    return "";
  }
}

/** 로그인 성공 시 호출. remember 면 이메일 저장, 아니면 저장분 삭제. */
export function saveRememberedEmail(email: string, remember: boolean): void {
  try {
    if (remember && email) {
      localStorage.setItem(REMEMBER_EMAIL_KEY, email);
    } else {
      localStorage.removeItem(REMEMBER_EMAIL_KEY);
    }
  } catch {
    /* localStorage 불가 환경 — 무시 */
  }
}
