import logging
from collections import defaultdict

from corgi.collectors.brew import Brew
from corgi.collectors.pulp import Pulp
from corgi.tasks.common import run_external

logger = logging.getLogger(__name__)

# Disable cache, subscription-manager plugins because they output stderr in OpenShift
DNF_BASE_COMMAND = ["dnf", "--noplugins", "--quiet"]


class Yum:
    """Get NVR / NEVRA / NSVCA data for an arbitrary repo using DNF commands.
    Usually the Pulp collector should be used instead.
    This Yum collector is only needed for community repos,
    or Red Hat repos which aren't tracked in Pulp.
    """

    def __init__(self, source: str = ""):
        self.brew = Brew(source)

    @staticmethod
    def filter_by_repos(command: list[str], repos: tuple[str, ...]) -> list[str]:
        """Take some DNF command and run it against only the repos specified."""
        repo_id = 1
        for repo_name in repos:
            command.append(f"--repofrompath={repo_id},{repo_name}")
            command.append(f"--repoid={repo_id}")
            repo_id += 1
        return command

    def get_modules(
        self, module_names: list[str], repos: tuple[str, ...]
    ) -> list[dict[str, dict[str, list[str]]]]:
        """Helper function to get specific data for modules in particular Yum repos."""
        command = DNF_BASE_COMMAND + ["module", "info"]
        command = self.filter_by_repos(command, repos)
        for module_name in module_names:
            command.append(module_name)
        _, output = run_external(command)

        modules_lines = []
        start_line_no = 0
        for line_no in range(len(output)):
            if output[line_no] == "":
                modules_lines.append(output[start_line_no:line_no])
                start_line_no = line_no + 1

        return [self.parse_module(lines) for lines in modules_lines]

    def find_modules_from_yum_repos(
        self, repos: tuple[str, ...]
    ) -> list[dict[str, dict[str, list[str]]]]:
        """List and collect info for all RPM modules in particular Yum repos."""
        command = DNF_BASE_COMMAND + ["module", "list"]
        command = self.filter_by_repos(command, repos)
        _, output = run_external(command)

        module_names = set()
        yum_modules = []
        # first line of the first block
        is_block_start = True
        for line in output:
            pieces = line.split()
            if line == "":
                # empty line indicates a new block
                is_block_start = True
                continue
            elif is_block_start:
                # first line is usually the number / name of the repository
                is_block_start = False
                continue
            elif len(pieces) > 1 and pieces[0] == "Name" and pieces[1] == "Stream":
                # second line has the names of the columns of the table
                continue
            elif pieces[0] == "Hint:":
                # last line is "Hint: [d]efault....."
                continue
            # everything else is a module name in the first column
            module_names.add(pieces[0])

        if module_names:
            yum_modules = self.get_modules(sorted(module_names), repos)

        return yum_modules

    @staticmethod
    def parse_module(lines: list[str]) -> dict[str, dict[str, list[str]]]:
        """Parse text data for a specific module into a Python dictionary"""
        res: dict[str, dict] = {"metadata": {"artifacts": []}}
        for current_line_no, line in enumerate(lines):
            pieces = line.split()

            if pieces[0] in ("Name", "Stream", "Version", "Context"):
                # Name             : python36
                # Stream           : 3.6 [d][e][a]
                # Version          : 820190123171828
                # Context          : 17efdbc7
                res["metadata"][pieces[0].lower()] = pieces[2]
            elif pieces[0] == "Artifacts":
                if len(pieces) > 2:
                    # Artifacts : python3-docs-0:3.6.7-1.module+el8+2339+1a6691f8.noarch
                    res["metadata"]["artifacts"].append(pieces[2])
                # For each remaining line, which all describe the artifacts
                # : python36-0:3.6.8-1.module+el8+2710+846623d6.x86_64
                for artifact_line in lines[current_line_no + 1 :]:
                    artifact_pieces = artifact_line.split()
                    if artifact_pieces[0] == ":" and len(artifact_pieces) > 1:
                        res["metadata"]["artifacts"].append(artifact_pieces[1])
                # assume Artifacts is at the end of the module info
                break
            # else ignore Architecture, Profiles, Repo, Summary, Description, etc.

        return res

    def get_nevras_from_yum_repos(
        self, repos: tuple[str, ...], latest: bool = False, ignore_source: bool = True
    ) -> dict[str, list[str]]:
        """Collect all NVR / NEVRAs for all SRPM / RPM pairs in particular Yum repos."""
        command = DNF_BASE_COMMAND + [
            "--disable-modular-filtering",
            "repoquery",
            "--all",
            "--queryformat",
            "%{sourcerpm} %{name}:%{epoch}-%{version}-%{release}.%{arch}",
        ]
        if latest:
            command.append("--latest-limit=1")
        command = self.filter_by_repos(command, repos)

        _, output = run_external(command)

        # For each line of output, split apart values like below:
        # "name-version-release.el8.src.rpm name:epoch-version-release.el8.noarch.rpm"
        # into separate NVRs (for SRPMs) and NEVRAs (for normal RPMs)
        nvr_nevra_mapping = defaultdict(list)
        for line in output:
            srpm, rpm = line.split()
            if rpm.endswith(".src"):
                # The first part is "(none)" and the second is the SRPM
                if ignore_source:
                    continue
                # Else the second part becomes the SRPM (without suffix) and the RPM is left blank
                srpm = rpm.replace(".src", "")
                rpm = ""
            else:
                # The first part is the SRPM, but Brew uses a different format
                srpm = srpm.replace(".src.rpm", "")
            nvr_nevra_mapping[srpm].append(rpm)
        return nvr_nevra_mapping

    def get_srpms_from_yum_repos(self, repos: tuple[str, ...]) -> tuple[str, ...]:
        """Return a list of build IDs, based on NVR / NEVRA pairs in some repo
        Normally returns the build ID for the NVR / SRPM build
        Returns a build ID for one of the NEVRAs / built RPMs, if the NVR couldn't be found"""
        nvr_nevra_mapping = self.get_nevras_from_yum_repos(repos, False, True)
        return tuple(self.brew.lookup_build_ids(nvr_nevra_mapping))

    def get_modules_from_yum_repos(self, repos: tuple[str, ...]) -> tuple[str, ...]:
        """Return a list of build IDs, based on NSVCA IDs in some repo"""
        modules_list = self.find_modules_from_yum_repos(repos)
        rpms_by_module = Pulp.get_rpms_by_module(modules_list)
        return tuple(self.brew.persist_modules(rpms_by_module))
