def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "requires_freecadcmd: test requires FreeCADCmd on PATH. "
        "Use '-m requires_freecadcmd and not requires_ccx and not requires_gmsh' "
        "to run FreeCADCmd-only tests.",
    )
    config.addinivalue_line(
        "markers",
        "requires_gmsh: test requires the gmsh Python package. "
        "Use '-m requires_gmsh and not requires_freecadcmd and not requires_ccx' "
        "to run gmsh-only tests.",
    )
    config.addinivalue_line(
        "markers",
        "requires_ccx: test requires ccx on PATH. "
        "Use '-m requires_ccx and not requires_freecadcmd and not requires_gmsh' "
        "to run ccx-only tests.",
    )
    config.addinivalue_line(
        "markers",
        "requires_runs: test requires at least one completed pipeline run in runs/. "
        "Auto-skipped when runs/ is empty.",
    )
    config.addinivalue_line(
        "markers",
        "requires_api: test requires ANTHROPIC_API_KEY or OPENAI_API_KEY. "
        "Use '-m requires_api' to run live LLM tests.",
    )
