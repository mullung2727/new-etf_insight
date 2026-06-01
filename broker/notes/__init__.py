"""투자노트 — 로컬 SQLite 저장 + CRUD.

broker 서버에 귀속되는 개인 데이터. 외부 LLM이 MCP 툴로 노트를 직접
작성/관리한다(broker는 LLM을 호출하지 않음). 저장 경로는 ``NOTES_DB_PATH``
환경변수로 바꿀 수 있어, GDrive 폴더 등에 두면 백업/동기화가 된다.
"""
