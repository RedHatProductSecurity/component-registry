import json
import logging
import shlex
import subprocess
from pathlib import Path
from typing import IO, Any, Generator, Optional

from splitstream import splitfile

from corgi.core.models import Component

logger = logging.getLogger(__name__)


class GoList:
    @classmethod
    def scan_files(cls, target_path: Path) -> Generator[dict[str, Any], None, None]:
        if not target_path.is_dir():
            raise ValueError("Target path %s is not a directory", target_path)
        # TODO unpack archives here?
        command = "/usr/bin/go list -json -deps ./..."
        yield from cls.invoke_process_popen_poll_live(command, target_path)

    @classmethod
    def invoke_process_popen_poll_live(
        cls, command: str, target_path: Path
    ) -> Generator[dict[str, Any], None, None]:
        """runs subprocess with Popen/poll until the subprocess exits"""
        # Let any exceptions propagate to the celery task
        process = subprocess.Popen(shlex.split(command), stdout=subprocess.PIPE, cwd=target_path)
        while True:
            yield from cls.parse_components(process.stdout)
            if process.poll() is not None:
                break

    @classmethod
    def parse_components(
        cls, go_list_pipe: Optional[IO[bytes]]
    ) -> Generator[dict[str, Any], None, None]:
        # use of splitstream here as `go list` output is actually a stream of json objects,
        # not a fully formed valid json document
        for jsonstr in splitfile(go_list_pipe, format="json"):
            artifact = json.loads(jsonstr)
            typed_component: dict[str, Any] = {
                "type": Component.Type.GOLANG,
                "meta": {
                    "name": artifact["ImportPath"],
                },
                "analysis_meta": {"source": "go-list"},
            }
            if "Module" in artifact:
                if "Version" in artifact["Module"]:
                    typed_component["version"] = artifact["Module"]["Version"]

            yield typed_component
