-- =====================================================================
-- 20261108 결제 테이블 통합 + 개인 계정 기반 가입 구조 전환 (PostgreSQL)
--
-- 1. credit_purchase_order 를 payment_order 로 통합 (product_type: credit | plan)
-- 2. organization_scenario (레거시 단일 시나리오) → organization_scenarios 이관 후 삭제
-- 3. account_profile 삭제 (역할은 조합 운영/소속 여부로 판단)
-- 4. 결제되지 않은 pending_payment 조합 삭제 (이제 조합은 플랜 결제 승인 시 생성)
--
-- 실행: psql "$POSTGRESQL_DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/20261108_unify_payment_personal_signup.sql
-- 신규 DB 는 이 스크립트 없이 서버 기동 시 create_all 로 새 구조가 생성된다.
-- =====================================================================
BEGIN;

-- ---------------------------------------------------------------------
-- 1. payment_order 확장 (기존 행은 모두 조합 플랜 결제)
-- ---------------------------------------------------------------------
ALTER TABLE payment_order
    ADD COLUMN product_type  VARCHAR(20),
    ADD COLUMN product_code  VARCHAR(20),
    ADD COLUMN item_name     VARCHAR(100),
    ADD COLUMN amount        INTEGER,
    ADD COLUMN credit_amount INTEGER,
    ADD COLUMN approved_at   TIMESTAMPTZ;

UPDATE payment_order
SET product_type = 'plan',
    product_code = plan_code,
    item_name    = '조합 ' || plan_code || ' 플랜',
    amount       = CASE plan_code
                       WHEN 'Standard' THEN 10000
                       WHEN 'Pro'      THEN 50000
                       WHEN 'Premium'  THEN 100000
                       ELSE 0
                   END,
    approved_at  = CASE WHEN status = 'approved' THEN created_at END;

ALTER TABLE payment_order DROP COLUMN plan_code;

-- organization_id: 플랜 결제 승인 후에만 채워지며, 조합이 삭제돼도 결제 이력은 남긴다
ALTER TABLE payment_order ALTER COLUMN organization_id DROP NOT NULL;
ALTER TABLE payment_order DROP CONSTRAINT IF EXISTS payment_order_organization_id_fkey;
ALTER TABLE payment_order
    ADD CONSTRAINT payment_order_organization_id_fkey
    FOREIGN KEY (organization_id) REFERENCES organization (organization_id) ON DELETE SET NULL;

-- ---------------------------------------------------------------------
-- 2. credit_purchase_order → payment_order 이관
-- ---------------------------------------------------------------------
INSERT INTO payment_order (
    partner_order_id, user_id, product_type, product_code, item_name,
    amount, credit_amount, organization_id, tid, status, created_at, approved_at
)
SELECT partner_order_id, user_id, 'credit', 'CREDIT_5', '개인 크레딧 5회권',
       price, credit_amount, NULL, tid, status, created_at,
       CASE WHEN status = 'approved' THEN created_at END
FROM credit_purchase_order;

DROP TABLE credit_purchase_order;

-- 제약조건 · 인덱스
ALTER TABLE payment_order
    ALTER COLUMN product_type SET NOT NULL,
    ALTER COLUMN product_code SET NOT NULL,
    ALTER COLUMN item_name    SET NOT NULL,
    ALTER COLUMN amount       SET NOT NULL;

ALTER TABLE payment_order
    ADD CONSTRAINT ck_payment_order_product_type
        CHECK (product_type IN ('credit', 'plan')),
    ADD CONSTRAINT ck_payment_order_status
        CHECK (status IN ('ready', 'approved', 'cancelled', 'failed')),
    ADD CONSTRAINT ck_payment_order_credit_amount
        CHECK ((product_type = 'credit' AND credit_amount IS NOT NULL)
            OR (product_type = 'plan'   AND credit_amount IS NULL));

CREATE INDEX IF NOT EXISTS ix_payment_order_user_id ON payment_order (user_id);

-- ---------------------------------------------------------------------
-- 3. 레거시 시나리오 이관 후 삭제
-- ---------------------------------------------------------------------
INSERT INTO organization_scenarios (organization_id, scenario_name, scenario_data, updated_by_user_id, updated_at)
SELECT organization_id, '기존 기준안', scenario_data, updated_by_user_id, updated_at
FROM organization_scenario;

DROP TABLE organization_scenario;

-- ---------------------------------------------------------------------
-- 4. account_profile 삭제
-- ---------------------------------------------------------------------
DROP TABLE account_profile;

-- ---------------------------------------------------------------------
-- 5. 결제되지 않은 조합 정리 (가입 시 생성됐던 pending_payment 조합)
--    해당 조합장 계정은 일반 개인 계정이 되고, 이후 플랜 결제 시 조합이 새로 생성된다
-- ---------------------------------------------------------------------
DELETE FROM organization
WHERE status = 'pending_payment' AND paid_until IS NULL;

ALTER TABLE organization ALTER COLUMN status SET DEFAULT 'active';

COMMIT;
