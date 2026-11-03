-- [읽기 전용] 소유자 UID 확인 — 상원님 본인 계정 이메일을 아래 자리에 넣고 실행(결과는 화면에서만 확인, 보고서에 이메일 적지 말 것)
-- 결과: 1행, email_confirmed=true 여야 seed_owner.sql 사용 가능.
select id as user_id, created_at, email_confirmed_at, (email_confirmed_at is not null) as email_confirmed
from auth.users
where lower(email) = lower(btrim('여기에-상원님-이메일'));
