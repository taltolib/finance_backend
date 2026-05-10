from fastapi import APIRouter, Header
from app.models.auth import PhoneRequest, CodeRequest
from app.services import auth_service

router = APIRouter()


@router.post("/send-code")
async def send_code(req: PhoneRequest):
    return await auth_service.send_telegram_code(req.phone)


@router.post("/verify-code")
async def verify_code(req: CodeRequest):
    return await auth_service.verify_telegram_code(
        phone=req.phone,
        phone_code_hash=req.phone_code_hash,
        code=req.code,
        password=req.password,
    )


@router.get("/me")
async def get_me(x_session_token: str = Header(...)):
    return await auth_service.get_current_user(x_session_token)


@router.post("/logout")
async def logout(x_session_token: str = Header(...)):
    return await auth_service.logout_user(x_session_token)