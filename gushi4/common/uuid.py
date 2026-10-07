import uuid

def generate_session_id() -> str:
    """生成唯一会话 SessionID"""
    return str(uuid.uuid4())