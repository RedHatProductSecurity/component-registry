import json
import logging
import shlex
import subprocess
import tempfile
from os import walk
from pathlib import Path
from shutil import ReadError, unpack_archive
from typing import IO, Any, Generator, Optional

from splitstream import splitfile

from corgi.core.models import Component

GO_LIST_COMMAND = "/usr/bin/go list -json -deps ./..."

logger = logging.getLogger(__name__)


class GoList:
    @classmethod
    def scan_files(cls, target_paths: list[Path]) -> list[dict[str, Any]]:
        results = []
        for target_path in target_paths:
            if not target_path.is_dir():
                with tempfile.TemporaryDirectory() as extract_dir:
                    try:
                        unpack_archive(target_path, extract_dir)
                    except ReadError:
                        logger.debug("Cannot unpack file: %s", target_path)
                        continue
                    go_source_dir = cls.find_go_dir(extract_dir)
                    if go_source_dir:
                        for result in cls.invoke_process_popen_poll_live(
                            GO_LIST_COMMAND, go_source_dir
                        ):
                            results.append(result)
                    else:
                        logger.debug("Did not find go.mod in %s", target_path)
            else:
                for result in cls.invoke_process_popen_poll_live(GO_LIST_COMMAND, target_path):
                    results.append(result)
        return results

    @classmethod
    def find_go_dir(cls, extract_dir):
        go_source_dir = None
        # Walk traverses directories in a top-down fashion meaning we can break on the first
        # detected go.mod file to avoid setting the root directory to a subdirectory by mistake
        for root, _, filenames in walk(extract_dir):
            if "go.mod" in filenames:
                go_source_dir = Path(root)
                break
        return go_source_dir

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
            # `go list` returns packages which are part of modules, as well as those which are part
            # of the standard library. Packages which are part of the standard library don't have a
            # version set so we do some post-processing in sca._scan_files to get the go standard
            # library version
            if "Module" in artifact:
                if "Version" in artifact["Module"]:
                    typed_component["version"] = artifact["Module"]["Version"]

            yield typed_component
