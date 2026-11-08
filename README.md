# 1. Overview

### 1.1. Tech Stack

# 2. 로컬 실행 방법

### 2.1. 가상 환경 설정

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

### 2.2. 가상 환경 필요 패키지 설치

```
pip install -U pip
pip install -r requirements.txt
```

### 2.2. 초기 .env 설정

```bash
cd BE
cp .env.example 
#이후, .env 파일 채우기 필요
```

### 2.3. Docker 실행

```bash
docker compose -f docker-compose.local.yml up --build
```

# 3. API 설명

# 4. 테스트 방법
- Unit Test

```bash
pytest -v
```

- Integration Test

```bash
docker compose -f docker-compose.local.yml up --build
pytest --integration -v
```


# 4. BE ERD

> 모든 사용자는 **개인 계정**으로 가입합니다. 이후 `CREDIT_5`(크레딧 5회권)를 결제하거나 조합 플랜(`Standard`/`Pro`/`Premium`)을 결제할 수 있으며,
> 첫 플랜 결제가 승인되는 시점에 `organization` 이 생성되고 결제자가 조합장이 됩니다. 결제 이력은 상품 종류와 관계없이 `payment_order` 한 테이블에 저장됩니다.

```mermaid
erDiagram
    user ||--o| organization : "leads (플랜 결제 시 생성)"
    user ||--o| organization_membership : "joins"
    organization ||--o{ organization_membership : "has members"
    organization ||--o{ organization_scenarios : "owns"
    user ||--o{ organization_scenarios : "updated_by"
    user ||--o{ payment_order : "pays"
    organization |o--o{ payment_order : "plan orders (SET NULL)"

    user {
        int user_id PK
        varchar user_name UK
        varchar email UK
        varchar password
    }

    organization {
        int organization_id PK
        int leader_user_id FK,UK
        varchar plan_code "Standard | Pro | Premium"
        varchar invitation_code UK
        varchar status "active | suspended"
        int max_members
        timestamptz paid_until
        timestamptz created_at
    }

    organization_membership {
        int membership_id PK
        int organization_id FK
        int user_id FK,UK
        varchar status "pending | active"
        timestamptz applied_at
        timestamptz approved_at
    }

    organization_scenarios {
        int scenario_id PK
        int organization_id FK
        varchar scenario_name
        json scenario_data
        int updated_by_user_id FK
        timestamptz updated_at
    }

    payment_order {
        int order_id PK
        varchar partner_order_id UK
        int user_id FK
        varchar product_type "credit | plan"
        varchar product_code "CREDIT_5 | Standard | Pro | Premium"
        varchar item_name
        int amount
        int credit_amount "credit 일 때만"
        int organization_id FK "plan 승인 후, nullable"
        varchar tid
        varchar status "ready | approved | cancelled | failed"
        timestamptz created_at
        timestamptz approved_at
    }
```

# 5. BE System Architecture

# 6. BE Directory Architecture
