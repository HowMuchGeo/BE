from pydantic import BaseModel, Field

#결제 준비 요청 스키마
class PaymentReadyRequest(BaseModel):
    #CREDIT_5 | Standard | Pro | Premium (GET /api/v1/payments/products 참고)
    product_code: str = Field(..., min_length=1, max_length=20)
