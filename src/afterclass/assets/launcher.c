// AfterClass.app's executable: runs `afterclass app` with Python embedded in this process, so macOS
// shows the bundle's name and icon in the Dock instead of "python3.14". Built by `afterclass install-app`
// (macapp.compile_launcher), which passes AFTERCLASS_PYTHON (the tool venv's python), AFTERCLASS_ARG
// and EXTRA_PATH with -D.

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdlib.h>
#include <string.h>

static int fail(PyConfig *config, PyStatus status) {
    PyConfig_Clear(config);
    if (PyStatus_IsExit(status)) return status.exitcode;
    Py_ExitStatusException(status);  // prints the error, exits 1
    return 1;
}

int main(int argc, char **argv) {
    // Finder starts apps without the shell PATH: add the usual tool locations (ffmpeg, tesseract, lms, claude…)
    const char *old = getenv("PATH");
    size_t n = strlen(EXTRA_PATH) + (old ? strlen(old) : 0) + 2;
    char *path = malloc(n);
    snprintf(path, n, "%s:%s", EXTRA_PATH, old ? old : "/usr/bin:/bin");
    setenv("PATH", path, 1);
    free(path);

    PyConfig config;
    PyConfig_InitPythonConfig(&config);
    config.parse_argv = 0;  // the arguments are afterclass's, not Python's
    // Python as if started from the venv: finds its pyvenv.cfg, stdlib and site-packages, and
    // sys.executable is that python (the app runs the pipeline with it)
    PyStatus status = PyConfig_SetBytesString(&config, &config.executable, AFTERCLASS_PYTHON);
    if (PyStatus_Exception(status)) return fail(&config, status);
    status = PyConfig_SetString(&config, &config.run_module, L"afterclass.cli");
    if (PyStatus_Exception(status)) return fail(&config, status);

    // argv: afterclass <AFTERCLASS_ARG> [own arguments, without the ones macOS adds]
    char **args = calloc(argc + 2, sizeof(char *));
    int count = 0;
    args[count++] = argv[0];
    args[count++] = AFTERCLASS_ARG;
    for (int i = 1; i < argc; i++) {
        if (strncmp(argv[i], "-psn", 4) && strncmp(argv[i], "-NS", 3)) args[count++] = argv[i];
    }
    status = PyConfig_SetBytesArgv(&config, count, args);
    free(args);
    if (PyStatus_Exception(status)) return fail(&config, status);

    status = Py_InitializeFromConfig(&config);
    if (PyStatus_Exception(status)) return fail(&config, status);
    PyConfig_Clear(&config);
    return Py_RunMain();
}
