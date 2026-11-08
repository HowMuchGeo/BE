from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database.orm import Base

#통합 결제 주문 DB 모델 (크레딧 충전 + 조합 플랜)
#상품명·금액은 주문 시점 값을 스냅샷으로 저장해 이후 가격이 바뀌어도 결제 이력이 보존된다
class PaymentOrder(Base):
    __tablename__ = "payment_order"
    __table_args__ = (
        CheckConstraint("product_type IN ('credit', 'plan')", name="ck_payment_order_product_type"),
        CheckConstraint("status IN ('ready', 'approved', 'cancelled', 'failed')", name="ck_payment_order_status"),
        CheckConstraint(
            "(product_type = 'credit' AND credit_amount IS NOT NULL) OR (product_type = 'plan' AND credit_amount IS NULL)",
            name="ck_payment_order_credit_amount",
        ),
    )

    order_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    #카카오페이에 전달하는 가맹점 주문번호
    partner_order_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    #credit | plan
    product_type: Mapped[str] = mapped_column(String(20), nullable=False)

    #CREDIT_5 | Standard | Pro | Premium
    product_code: Mapped[str] = mapped_column(String(20), nullable=False)

    item_name: Mapped[str] = mapped_column(String(100), nullable=False)

    amount: Mapped[int] = mapped_column(Integer, nullable=False)

    #크레딧 상품일 때만 값이 있다
    credit_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #플랜 결제 승인 후 생성·연장된 조합. 조합이 삭제돼도 결제 이력은 남긴다
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organization.organization_id", ondelete="SET NULL"),
        nullable=True,
    )

    tid: Mapped[str | None] = mapped_column(String(128), nullable=True)

    #ready | approved | cancelled | failed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ready")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
