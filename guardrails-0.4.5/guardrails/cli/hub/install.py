from contextlib import contextmanager
import os
import subprocess
import sys
from string import Template
from typing import List, Literal
import shutil
import signal
import tomllib

import typer

from guardrails.classes.generic import Stack
from guardrails.cli.hub.hub import hub_command
from guardrails.cli.logger import LEVELS, logger
from guardrails.cli.server.hub_client import get_validator_manifest
from guardrails.cli.server.module_manifest import ModuleManifest

from guardrails.cli.hub.utils import pip_process
from guardrails.cli.hub.utils import get_site_packages_location
from guardrails.cli.hub.utils import get_org_and_package_dirs
from guardrails.cli.hub.utils import get_hub_directory
from guardrails.cli.hub import manifest_jsons

from .console import console

def install_dependencies_from_pyproject(pyproject_path):
    with open(pyproject_path, 'rb') as f:
        pyproject_data = tomllib.load(f)

    # Extract standard dependencies
    dependencies = pyproject_data['project']['dependencies']
    for dep in dependencies:
        subprocess.run(["pip", "install", dep], check=True)

    # Extract optional dependencies under 'dev'
    optional_dependencies = pyproject_data['project']['optional-dependencies']['dev']
    for dep in optional_dependencies:
        subprocess.run(["pip", "install", dep], check=True)

def get_json_by_name(name):
    return ModuleManifest.from_dict(manifest_jsons.manifests.get(name))

def removesuffix(string: str, suffix: str) -> str:
    if sys.version_info.minor >= 9:
        return string.removesuffix(suffix)  # type: ignore
    else:
        if string.endswith(suffix):
            return string[: -len(suffix)]
        return string


string_format: Literal["string"] = "string"
json_format: Literal["json"] = "json"


# NOTE: I don't like this but don't see another way without
#  shimming the init file with all hub validators
def add_to_hub_inits(manifest: ModuleManifest, site_packages: str):
    org_package = get_org_and_package_dirs(manifest)
    exports: List[str] = manifest.exports or []
    sorted_exports = sorted(exports, reverse=True)
    module_name = manifest.module_name
    relative_path = ".".join([*org_package, module_name])
    import_line = (
        f"from guardrails.hub.{relative_path} import {', '.join(sorted_exports)}"
    )

    hub_init_location = os.path.join(site_packages, "guardrails", "hub", "__init__.py")
    with open(hub_init_location, "a+") as hub_init:
        hub_init.seek(0, 0)
        content = hub_init.read()
        if import_line in content:
            hub_init.close()
        else:
            hub_init.seek(0, 2)
            if len(content) > 0:
                hub_init.write("\n")
            hub_init.write(import_line)
            hub_init.close()

    namespace = org_package[0]
    namespace_init_location = os.path.join(
        site_packages, "guardrails", "hub", namespace, "__init__.py"
    )
    if os.path.isfile(namespace_init_location):
        with open(namespace_init_location, "a+") as namespace_init:
            namespace_init.seek(0, 0)
            content = namespace_init.read()
            if import_line in content:
                namespace_init.close()
            else:
                namespace_init.seek(0, 2)
                if len(content) > 0:
                    namespace_init.write("\n")
                namespace_init.write(import_line)
                namespace_init.close()
    else:
        with open(namespace_init_location, "w") as namespace_init:
            namespace_init.write(import_line)
            namespace_init.close()


def run_post_install(manifest: ModuleManifest, site_packages: str):
    org_package = get_org_and_package_dirs(manifest)
    post_install_script = manifest.post_install

    if not post_install_script:
        return

    module_name = manifest.module_name
    relative_path = os.path.join(
        site_packages,
        "guardrails",
        "hub",
        *org_package,
        module_name,
        post_install_script,
    )

    if os.path.isfile(relative_path):
        try:
            logger.debug("running post install script...")
            command = [sys.executable, relative_path]
            subprocess.check_output(command)
        except subprocess.CalledProcessError as exc:
            logger.error(
                (
                    f"Failed to run post install script for {manifest.id}\n"
                    f"Exit code: {exc.returncode}\n"
                    f"stdout: {exc.output}"
                )
            )
            sys.exit(1)
        except Exception as e:
            logger.error(
                f"An unexpected exception occurred while running the post install script for {manifest.id}!",  # noqa
                e,
            )
            sys.exit(1)


def get_install_url(manifest: ModuleManifest) -> str:
    repo = manifest.repository
    repo_url = repo.url
    branch = repo.branch

    git_url = repo_url
    if not repo_url.startswith("git+"):
        git_url = f"git+{repo_url}"

    if branch is not None:
        git_url = f"{git_url}@{branch}"

    return git_url


def install_hub_module(
    module_manifest: ModuleManifest, site_packages: str, quiet: bool = False, ld: str = None
):
    
    #print("\n\nI AM IN HEREERERERERERERERE\n\n")
    install_url = get_install_url(module_manifest)
    install_directory = get_hub_directory(module_manifest, site_packages)
    
    pip_flags = [f"--target={install_directory}", "--no-deps"]
    if quiet:
        pip_flags.append("-q")

    # Install validator module in namespaced directory under guardrails.hub
    #print("\n\n\nLOCATION OF installation:  ", install_directory, "\n")
    #print("\n\n\ninstal_url:  ", install_url, "\n")

    if ld is None:
        download_output = pip_process("install", install_url, pip_flags, quiet=quiet)

        #print("\n\nDownload_output = ", download_output, "\n\n")
        if not quiet:
            logger.info(download_output)
        # Install validator module's dependencies in normal site-packages directory
        inspect_output = pip_process(
            "inspect",
            flags=[f"--path={install_directory}"],
            format=json_format,
            quiet=quiet,
            no_color=True,
        )
        
        #print("\n\n\nINSPECT OUTPUT = ", inspect_output, "\n\n\n")

        # throw if inspect_output is a string. Mostly for pyright
        if isinstance(inspect_output, str):
            logger.error("Failed to inspect the installed package!")
            sys.exit(1)

        dependencies = (
            Stack(*inspect_output.get("installed", []))
            .at(0, {})
            .get("metadata", {})  # type: ignore
            .get("requires_dist", [])  # type: ignore
        )
        requirements = list(filter(lambda dep: "extra" not in dep, dependencies))
        for req in requirements:
            if "git+" in req:
                install_spec = req.replace(" ", "")
                dep_install_output = pip_process("install", install_spec, quiet=quiet)
                if not quiet:
                    logger.info(dep_install_output)
            else:
                req_info = Stack(*req.split(" "))
                name = req_info.at(0, "").strip()  # type: ignore
                versions = req_info.at(1, "").strip("()")  # type: ignore
                if name:
                    install_spec = name if not versions else f"{name}{versions}"
                    dep_install_output = pip_process("install", install_spec, quiet=quiet)
                    if not quiet:
                        logger.info(dep_install_output)
    else:
        try:
            shutil.copytree(ld, install_directory)
            install_dependencies_from_pyproject(os.path.join(ld, "pyproject.toml"))

        except Exception as e:
            print("\nFailed to copy file from source to destination folder.\nMake sure that source directory exists.")
            sys.exit(1)

    


@hub_command.command()
def install(
    package_uri: str = typer.Argument(
        help="URI to the package to install.\
Example: hub://guardrails/regex_match."
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        help="Run the command in quiet mode to reduce output verbosity.",
    ),
    hubManifest: bool = typer.Option(
        False,
        "--hubmanifest",
        help="When enabled, will pull module manifest from GR-AI server.",
    ),
    localDownload: str = typer.Option(
        None,
        "--localdownload",
        help="Path to the validator folder for local download.",
    ),
):
    print("\n\n\nValue of hubman = ", hubManifest)
    print("localdownload = ", localDownload)
    
    verbose_printer = console.print
    quiet_printer = console.print if not quiet else lambda x: None
    """Install a validator from the Hub."""
    if not package_uri.startswith("hub://"):
        logger.error("Invalid URI!")
        sys.exit(1)

    installing_msg = f"Installing {package_uri}..."
    logger.log(
        level=LEVELS.get("SPAM"),  # type: ignore
        msg=installing_msg,
    )
    verbose_printer(installing_msg)

    # Validation
    module_name = package_uri.replace("hub://", "")

    @contextmanager
    def do_nothing_context(*args, **kwargs):
        try:
            yield
        finally:
            pass

    loader = console.status if not quiet else do_nothing_context

    # Prep
    fetch_manifest_msg = "Fetching manifest"
    with loader(fetch_manifest_msg, spinner="bouncingBar"):
        if(not hubManifest):
            module_manifest = get_validator_manifest(module_name)
        else:
            module_manifest = get_json_by_name(module_name)
        site_packages = get_site_packages_location()

    # Install
    dl_deps_msg = "Downloading dependencies"
    with loader(dl_deps_msg, spinner="bouncingBar"):
        install_hub_module(module_manifest, site_packages, quiet=quiet, ld = localDownload)

    # Post-install
    post_msg = "Running post-install setup"
    with loader(post_msg, spinner="bouncingBar"):
        run_post_install(module_manifest, site_packages)
        add_to_hub_inits(module_manifest, site_packages)

    logger.info("Installation complete")

    verbose_printer(f"✅Successfully installed {module_name}!\n\n")
    success_message_cli = Template(
        "[bold]Import validator:[/bold]\n"
        "from guardrails.hub import ${export}\n\n"
        "[bold]Get more info:[/bold]\n"
        "https://hub.guardrailsai.com/validator/${id}\n"
    ).safe_substitute(
        module_name=package_uri,
        id=module_manifest.id,
        export=module_manifest.exports[0],
    )
    success_message_logger = Template(
        "✅Successfully installed ${module_name}!\n\n"
        "Import validator:\n"
        "from guardrails.hub import ${export}\n\n"
        "Get more info:\n"
        "https://hub.guardrailsai.com/validator/${id}\n"
    ).safe_substitute(
        module_name=package_uri,
        id=module_manifest.id,
        export=module_manifest.exports[0],
    )
    quiet_printer(success_message_cli)  # type: ignore
    logger.log(level=LEVELS.get("SPAM"), msg=success_message_logger)  # type: ignore
