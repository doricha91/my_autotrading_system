# logging_setup.py
# 📝 봇의 모든 활동을 기록하는 로거를 설정합니다.

import logging
from logging.handlers import TimedRotatingFileHandler
import os


def setup_logger():
    """
    스트림 핸들러와 파일 핸들러를 포함하는 로거를 설정합니다.
    - INFO 레벨 이상의 로그는 콘솔에 출력됩니다.
    - INFO 레벨 이상의 모든 로그는 날짜별로 'autotrading.log' 파일에 기록됩니다.
    """
    # 루트 로거를 가져옵니다.
    logger = logging.getLogger()

    # 이미 핸들러가 설정되어 있다면, 중복 추가를 방지하기 위해 그냥 반환합니다.
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    # 로그 포맷을 정의합니다.
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s')

    # 1. 콘솔(스트림) 핸들러 설정
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    # 2. 파일 핸들러 설정
    # 로그 파일을 저장할 'logs' 디렉토리 생성
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    log_file_path = os.path.join(log_dir, "autotrading.log")

    # TimedRotatingFileHandler: 자정마다 로그 파일을 새로 생성하며, 이전 파일은 날짜가 붙어 백업됩니다.
    file_handler = TimedRotatingFileHandler(
        log_file_path,
        when="midnight",  # 자정에 롤오버
        interval=1,  # 매일
        backupCount=30,  # 30일치 로그 보관
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger