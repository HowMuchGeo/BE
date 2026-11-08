from typing import Literal
from pydantic import BaseModel

ProductType = Literal["credit", "plan"]

#결제 상품 응답 스키마
class ProductResponse(BaseModel):
    product_code: str
    product_type: ProductType
    name: str
    price: int
    credit_amount: int | None = None
    max_members: int | None = None
    duration_months: int | None = None

#결제 준비 응답 스키마
class PaymentReadyResponse(BaseModel):
    next_redirect_pc_url: str
    tid: str
    product_code: str
    product_type: ProductType
    item_name: str
    price: int
    credit_amount: int | None = None
