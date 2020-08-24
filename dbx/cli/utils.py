import datetime as dt
import json
from typing import Dict, Any, List, Tuple

import click
import mlflow
from databricks_cli.configure.provider import ProfileConfigProvider
from databricks_cli.sdk.api_client import ApiClient

INFO_FILE_NAME = ".dbx/project.json"
DATABRICKS_MLFLOW_URI = "databricks"


def read_json(file_path: str) -> Dict[str, Any]:
    with open(file_path, "r") as f:
        return json.load(f)


def write_json(content: Dict[str, Any], file_path: str):
    with open(file_path, "w") as f:
        json.dump(content, f, indent=4)


def update_json(new_content: Dict[str, Any], file_path: str):
    content = read_json(file_path)
    content.update(new_content)
    write_json(content, file_path)


class InfoFile:

    @staticmethod
    def initialize(content: Dict[str, Any]):
        write_json(content, INFO_FILE_NAME)

    @staticmethod
    def update(content: Dict[str, Any]) -> None:
        update_json(content, INFO_FILE_NAME)

    @staticmethod
    def get(item: str) -> Any:
        return read_json(INFO_FILE_NAME).get(item)


def dbx_echo(message: str):
    formatted_message = "[dbx][%s] %s" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], message)
    click.echo(formatted_message)


def parse_params(params: List[str]):
    contains_equals = sum(["=" in t for t in params])
    if contains_equals == len(params):
        # if the format is --tag1=value2 --tag2=value2
        formatted = {t.split("=")[0].replace("--", "").replace("-", "_"): t.split("=")[-1] for t in params}
    else:
        # format is --tag1 value1 --tag2 value2
        if len(params) % 2 != 0:
            raise NameError("Given tags are not in compatible format (either --param1=value1 or --param1 value1).")
        else:
            keys = params[::2]
            values = params[1::2]
            formatted = {keys[idx].replace("--", "").replace("-", "_"): values[idx] for idx in range(len(keys))}

    return formatted


def generate_filter_string(env: str, tags: Dict[str, str]):
    tags_filter = ['tags.%s="%s"' % (key, value) for key, value in tags.items()]
    env_filter = ['tags.dbx_environment="%s"' % env]

    # we are not using attribute.status due to it's behaviour with nested runs
    status_filter = ['tags.dbx_status="SUCCESS"']
    deploy_filter = ['tags.dbx_action_type="deploy"']

    filters = tags_filter + status_filter + deploy_filter + env_filter
    filter_string = " and ".join(filters)
    return filter_string


def _provide_environment(environment: str) -> Tuple[Dict[str, Any], ApiClient]:
    environment_data = InfoFile.get("environments").get(environment)
    mlflow.set_tracking_uri("%s://%s" % (DATABRICKS_MLFLOW_URI, environment_data["profile"]))
    mlflow.set_experiment(environment_data["workspace_dir"])
    profile_config = ProfileConfigProvider(environment_data["profile"]).get_config()
    api_client = ApiClient(host=profile_config.host, token=profile_config.token)
    return environment_data, api_client
