"""
Helper code to move all Celery task names in all sibling modules into a common namespace
Eg. corgi.tasks.brew.slow_fetch_brew_build is also corgi.tasks.tasks.slow_fetch_brew_build
Needed in order to discover new tasks without config changes whenever a new submodule is added
Note that either name above will work to import and run it in a Python shell
But Celery uses the name of the module where the app.task decorator was applied, i.e. 1st form
"""
import importlib as _importlib
import pkgutil as _pkgutil

# 1. Walk the tree of modules underneath the corgi.tasks package
# 2. Discover, import, and introspect all the methods
# 3. Add names that are Celery tasks to globals(), AKA this namespace

for _loader, _module_name, _is_pkg in _pkgutil.walk_packages((__file__.replace("/tasks.py", ""),)):
    if _is_pkg or _module_name == "tasks":
        # Skip subpackages / management commands and self
        continue
    # Else it's a sibling module - import all its methods so we can introspect them
    # MetaPathFinder wants a path argument, but PathEntryFinder won't accept a path argument
    if not isinstance(_loader, _importlib.abc.PathEntryFinder):
        raise ValueError(f"Got wrong loader type for {_module_name}: {type(_loader)}")
    _module_loader = _loader.find_module(f"corgi.tasks.{_module_name}")
    if not _module_loader:
        raise ValueError(f"Couldn't find module corgi.tasks.{_module_name}")
    _module = _module_loader.load_module(f"corgi.tasks.{_module_name}")

    for _method_name in dir(_module):
        if _method_name.startswith("_"):
            # Skip builtins / private names
            continue

        # Convert method name into object and introspect it
        _method = getattr(_module, _method_name)
        if hasattr(_method, "app") and hasattr(_method, "__wrapped__"):
            # It's a method wrapped with an app.task decorator, AKA a Celery task
            # Check both to avoid importing methods wrapped with non-Celery decorators
            # Or importing Celery methods like chain which are not tasks
            globals()[_method_name] = _method

        # Else it's an imported name, class constant, or non-task helper method
        # We skip importing these when running "from corgi.tasks import *"
