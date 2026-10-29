"""Module entry point."""

import sys

if "--procedure-broker" in sys.argv:
    from materials_data_studio.tools.procedure_sandbox import main
elif "--procedure-worker" in sys.argv or "--sandbox-probe" in sys.argv:
    from materials_data_studio.tools.procedure_worker import main
else:
    from materials_data_studio.bootstrap import main

if __name__ == "__main__":
    raise SystemExit(main())

