from datetime import timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional

import logging

logger = logging.getLogger(__name__)


def get_dag_id(dag_file: str) -> str:
    """
    DAG 파일의 디렉토리 구조를 기반으로 DAG ID를 생성합니다.

    가장 마지막 'dags' 디렉토리부터 파일까지의 경로를 '__'로 연결하여 DAG ID를 만듭니다.
    예: /usr/local/airflow/dags/etl/api_to_postgresql/dag.py -> etl__api_to_postgresql__dag

    Args:
        dag_file (str): DAG 파일의 전체 경로

    Returns:
        str: 생성된 DAG ID

    Raises:
        ValueError: 'dags' 디렉토리를 찾을 수 없는 경우
    """
    dag_path = Path(dag_file).resolve()
    parts = dag_path.parts

    # 역순으로 순회하여 첫 번째 'dags' 찾기 (가장 마지막 dags)
    try:
        dags_index = len(parts) - 1 - parts[::-1].index('dags')
    except ValueError:
        raise ValueError(f"'dags' 디렉토리를 찾을 수 없습니다: {dag_file}")

    # dags 이후 경로들 + 파일명(확장자 제외) 결합
    path_components = parts[dags_index + 1:-1] + (dag_path.stem,)

    return '__'.join(path_components)


def get_default_args(retries: int = 2,
                     retry_delay_minutes: int = 5) -> Dict[str, Any]:
    """
    DAG의 기본 인자를 생성합니다.

    Args:
        retries (int): 재시도 횟수 (기본값: 2)
        retry_delay_minutes (int): 재시도 간격(분) (기본값: 5)

    Returns:
        Dict[str, Any]: 기본 인자 딕셔너리
    """
    default_args = {
        'owner': 'ybigta-mlops',
        'depends_on_past': False,
        'retries': retries,
        'retry_delay': timedelta(minutes=retry_delay_minutes),
    }

    return default_args

