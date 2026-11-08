from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database.orm import Base

#조직(조합) DB 모델
#플랜 결제가 승인되는 시점에 생성된다. 결제 전 대기 상태(pending_payment)는 더 이상 존재하지 않는다
class Organization(Base):
    __tablename__ = "organization"

    organization_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True
    )

    #조합장. 한 사용자는 하나의 조합만 운영할 수 있다
    leader_user_id: Mapped[int] = mapped_column(
        ForeignKey("user.user_id", ondelete="CASCADE"),
        unique=True,
        nullable=False
    )

    #현재 적용 중인 플랜 (갱신 결제 시 변경될 수 있음)
    plan_code: Mapped[str] = mapped_column(
        String(20),
        nullable=False
    )

    invitation_code: Mapped[str] = mapped_column(
        String(32),
        unique=True,
        nullable=False
    )

    #active | suspended. 만료 여부는 paid_until 로 판단한다
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="active"
    )

    max_members: Mapped[int] = mapped_column(
        Integer,
        nullable=False
    )

    paid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

#조직 멤버십 DB 모델
class OrganizationMembership(Base):
    __tablename__ = "organization_membership"
    __table_args__ = (UniqueConstraint("user_id", name="uq_org_membership_user"),)

    membership_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True
    )

    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organization.organization_id", ondelete="CASCADE"),
        nullable=False
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.user_id", ondelete="CASCADE"),
        nullable=False
    )

    #pending | active
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending"
    )

    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )
