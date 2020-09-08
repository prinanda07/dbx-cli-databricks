import base64
import json
from typing import Dict, Any

import click
import mlflow
import time
from databricks_cli.dbfs.api import DbfsService
from databricks_cli.jobs.api import JobsService
from databricks_cli.sdk.api_client import ApiClient
from databricks_cli.utils import CONTEXT_SETTINGS

from dbx.cli.utils import dbx_echo, _generate_filter_string, _provide_environment, environment_option


@click.command(context_settings=CONTEXT_SETTINGS,
               short_help="Launches job, choosing the latest version by given tags.")
@click.option("--environment", required=True, type=str, help="Environment name.")
@click.option("--job", required=True, type=str, help="Job name.")
@click.option("--trace", is_flag=True, help="Trace the job until it finishes.")
@click.option("--existing-runs", type=click.Choice(["wait", "cancel"]), default="wait",
              help="Strategy to work with existing job runs")
@environment_option
def launch(environment: str, job: str, trace: bool, existing_runs: str):
    dbx_echo("Launching job by given parameters")

    environment_data, api_client = _provide_environment(environment)

    filter_string = _generate_filter_string(environment)

    runs = mlflow.search_runs(experiment_ids=environment_data["experiment_id"],
                              filter_string=filter_string,
                              max_results=1)

    if runs.empty:
        raise EnvironmentError("""
        No runs provided per given set of filters:
            %s
        Please check filters experiment UI to verify current status of deployments.
        """ % filter_string)

    run_info = runs.iloc[0].to_dict()

    deployment_run_id = run_info["run_id"]

    with mlflow.start_run(run_id=deployment_run_id) as deployment_run:
        with mlflow.start_run(nested=True):

            artifact_base_uri = deployment_run.info.artifact_uri
            deployments = _load_deployments(api_client, artifact_base_uri)
            job_id = deployments.get(job)

            if not job_id:
                raise Exception("Job with name %s not found in the latest deployment" % job)

            jobs_service = JobsService(api_client)
            active_runs = jobs_service.list_runs(job_id, active_only=True).get("runs", [])

            for run in active_runs:

                if existing_runs == "wait":
                    dbx_echo("Waiting for job run with id %s to be finished" % run["run_id"])
                    _wait_run(api_client, run)

                if existing_runs == "cancel":
                    dbx_echo("Cancelling run with id %s" % run["run_id"])
                    _cancel_run(api_client, run)

            run_data = jobs_service.run_now(job_id)

            if trace:
                dbx_status = _trace_run(api_client, run_data)
            else:
                dbx_status = "NOT_TRACKED"

            deployment_tags = {
                "job_id": job_id,
                "run_id": run_data["run_id"],
                "dbx_action_type": "launch",
                "dbx_status": dbx_status,
                "dbx_environment": environment
            }

            mlflow.set_tags(deployment_tags)


def _cancel_run(api_client: ApiClient, run_data: Dict[str, Any]):
    api_client.perform_query('POST', '/jobs/runs/cancel', data={"run_id": run_data["run_id"]}, headers=None)
    while True:
        time.sleep(5)  # runs API is eventually consistent, it's better to have a short pause for status update
        status = _get_run_status(api_client, run_data)
        result_state = status["state"].get("result_state", None)
        if result_state:
            return None


def _load_deployments(api_client: ApiClient, artifact_base_uri: str):
    dbfs_service = DbfsService(api_client)
    dbx_deployments = "%s/.dbx/deployments.json" % artifact_base_uri
    raw_config_payload = dbfs_service.read(dbx_deployments)["data"]
    payload = base64.b64decode(raw_config_payload).decode("utf-8")
    deployments = json.loads(payload)
    return deployments


def _wait_run(api_client: ApiClient, run_data: Dict[str, Any]):
    while True:
        time.sleep(5)  # runs API is eventually consistent, it's better to have a short pause for status update
        status = _get_run_status(api_client, run_data)
        result_state = status["state"].get("result_state", None)
        if result_state:
            return None


def _trace_run(api_client: ApiClient, run_data: Dict[str, Any]) -> str:
    dbx_echo("Tracing job run")
    while True:
        status = _get_run_status(api_client, run_data)
        result_state = status["state"].get("result_state", None)
        if result_state:
            if result_state == "SUCCESS":
                dbx_echo("Job run finished successfully")
                return "SUCCESS"
            else:
                return "ERROR"
        else:
            dbx_echo("Job run is not yet finished, current status message: %s" % status["state"]["state_message"])
            time.sleep(5)


def _get_run_status(api_client: ApiClient, run_data: Dict[str, Any]) -> Dict[str, Any]:
    run_status = api_client.perform_query('GET', '/jobs/runs/get', data={"run_id": run_data["run_id"]}, headers=None)
    return run_status
