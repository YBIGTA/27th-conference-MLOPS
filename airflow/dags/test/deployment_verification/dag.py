"""
Lightsail CICD 연동 확인을 위한 배포 검증 DAG

이 DAG는 Lightsail CICD 파이프라인이 정상적으로 동작하는지 확인하기 위한 간단한 테스트 DAG입니다.
"""
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.bash import BashOperator
import sys
from pathlib import Path

# utils 경로 추가
utils_path = Path(__file__).resolve().parents[3] / 'utils'
sys.path.insert(0, str(utils_path))

from dag_utils import get_dag_id, get_default_args


def print_deployment_info(**context):
    """배포 환경 정보를 출력합니다."""
    import socket
    import os

    print("=" * 50)
    print("Deployment Verification Check")
    print("=" * 50)
    print(f"Hostname: {socket.gethostname()}")
    print(f"Python Version: {sys.version}")
    print(f"Current Time: {datetime.now()}")
    print(f"Airflow Home: {os.environ.get('AIRFLOW_HOME', 'Not Set')}")
    print(f"Execution Date: {context['execution_date']}")
    print(f"DAG ID: {context['dag'].dag_id}")
    print("=" * 50)
    print("✅ Lightsail CICD Integration Verified!")
    print("=" * 50)


def check_dag_utils():
    """dag_utils가 정상적으로 import되는지 확인합니다."""
    print("✅ dag_utils module imported successfully!")
    print(f"DAG ID generated from dag_utils: {get_dag_id(__file__)}")
    print(f"Default args: {get_default_args()}")


# DAG 기본 인자 설정
default_args = get_default_args(retries=1, retry_delay_minutes=1)

# DAG 정의
with DAG(
    dag_id=get_dag_id(__file__),
    default_args=default_args,
    description='Lightsail CICD 연동 확인을 위한 배포 검증 DAG',
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=['test', 'deployment', 'cicd', 'verification'],
) as dag:

    # Task 1: 시스템 정보 확인
    system_check = BashOperator(
        task_id='system_check',
        bash_command='echo "System Check: $(date)" && echo "User: $(whoami)" && echo "Working Directory: $(pwd)"',
    )

    # Task 2: dag_utils 모듈 확인
    utils_check = PythonOperator(
        task_id='dag_utils_check',
        python_callable=check_dag_utils,
    )

    # Task 3: 배포 정보 출력
    deployment_info = PythonOperator(
        task_id='print_deployment_info',
        python_callable=print_deployment_info,
    )

    # Task 4: 최종 확인
    final_check = BashOperator(
        task_id='final_verification',
        bash_command='echo "🎉 All checks passed! Lightsail CICD is working properly!"',
    )

    # Task 의존성 설정
    system_check >> \
    utils_check >> \
    deployment_info >> \
    final_check
