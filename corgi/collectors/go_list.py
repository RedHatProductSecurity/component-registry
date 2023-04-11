import json
import logging
import shlex
import subprocess
import tempfile
from json import JSONDecodeError
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
        results: list[dict[str, Any]] = []
        for target_path in target_paths:
            if not target_path.is_dir():
                with tempfile.TemporaryDirectory() as extract_dir:
                    try:
                        unpack_archive(target_path, extract_dir)
                    except ReadError:
                        logger.debug("Cannot unpack file: %s", target_path)
                        continue
                    logger.debug("Running 'go list' scan on %s", target_path)
                    cls._check_and_scan(extract_dir, results, str(target_path))
            else:
                cls._check_and_scan(str(target_path), results)
        return results

    @classmethod
    def _check_and_scan(cls, extract_dir: str, results, target_path: str = ""):
        go_source_dir = cls.find_go_dir(extract_dir)
        if go_source_dir:
            for result in cls.invoke_process_popen_poll_live(GO_LIST_COMMAND, go_source_dir):
                results.append(result)
        else:
            if not target_path:
                target_path = extract_dir
            logger.debug("Did not find go.mod in %s", target_path)

    @classmethod
    def find_go_dir(cls, extract_dir: str) -> Optional[Path]:
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
        with subprocess.Popen(
            shlex.split(command), stdout=subprocess.PIPE, cwd=target_path
        ) as process:
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
            try:
                artifact = json.loads(jsonstr)
            except JSONDecodeError:
                logger.warning("Unable to parse %s as json", jsonstr)
                continue
            if "ImportPath" not in artifact:
                logger.warning(f"Did not find ImportPath in artifact: {artifact}")
                continue
            typed_component: dict[str, Any] = {
                "type": Component.Type.GOLANG,
                "namespace": Component.Namespace.UPSTREAM,
                "meta": {
                    "name": artifact["ImportPath"],
                    "go_component_type": "go-package",
                    "source": ["go-list"],
                },
            }
            # `go list` returns packages which are part of modules, as well as those which are part
            # of the standard library. Packages which are part of the standard library don't have a
            # version set so we do some post-processing in sca._scan_files to get the go standard
            # library version
            if "Module" in artifact:
                if "Version" in artifact["Module"]:
                    typed_component["version"] = artifact["Module"]["Version"]

            yield typed_component
