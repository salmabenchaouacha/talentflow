FROM apache/airflow:2.10.3-python3.12
USER root
COPY --chown=airflow:root . /opt/talentflow
USER airflow
RUN pip install --no-cache-dir "/opt/talentflow[postgres]" dbt-postgres==1.8.*
