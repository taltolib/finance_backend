from pydantic import BaseModel
from typing import Optional

class PhoneRequest(BaseModel):
    phone: str

class CodeRequest(BaseModel):
    phone: str
    phone_code_hash: str
    code: str
    password: Optional[str] = None