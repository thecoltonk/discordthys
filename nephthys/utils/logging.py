import logging
from base64 import b64encode
from os import environ
from os import uname
from uuid import uuid4

from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs import LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource


def parse_level_name(level_name: str | int) -> int:
    if isinstance(level_name, int):
        return level_name
    name = level_name.upper()
    if name == "VERBOSE":
        name = "DEBUG"
    name_to_int = logging.getLevelNamesMapping()
    try:
        return name_to_int[name]
    except KeyError:
        raise ValueError(f"Invalid log level: {level_name}")


def setup_otel_logging(
    *,
    endpoint: str,
    service_name: str,
    environment: str,
    basic_auth: str | None,
    level: str | int,
):
    """Set up OpenTelemetry log export."""
    resource_attributes = {
        "service.name": service_name,
        "service.instance.id": uuid4().hex,
        "deployment.environment": environment,
        "host.name": uname().nodename,
    }
    commit_hash = environ.get("SOURCE_COMMIT")
    if commit_hash:
        resource_attributes["service.version"] = commit_hash
    provider = LoggerProvider(resource=Resource.create(resource_attributes))
    set_logger_provider(provider)

    auth_header = (
        "Basic " + b64encode(basic_auth.encode()).decode() if basic_auth else None
    )
    otlp_exporter = OTLPLogExporter(
        endpoint=endpoint,
        headers={"Authorization": auth_header} if auth_header else {},
    )
    provider.add_log_record_processor(BatchLogRecordProcessor(otlp_exporter))

    # Don't allow DEBUG logs to be sent via HTTP, because that creates infinite recursive HTTP request logs
    log_level = max(parse_level_name(level), logging.INFO)
    handler = LoggingHandler(level=log_level, logger_provider=provider)

    logging.getLogger().addHandler(handler)
    logging.info("Initialised OpenTelemetry logs exporter from environment variables")
