#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>
#include <wchar.h>

typedef int (__cdecl *PythonMain)(int, wchar_t **);

static wchar_t *next_line(wchar_t *text)
{
    wchar_t *end = text ? wcschr(text, L'\n') : NULL;
    if (!end) return NULL;
    *end = L'\0';
    if (end > text && end[-1] == L'\r') end[-1] = L'\0';
    return end + 1;
}

static int fail(const wchar_t *message, DWORD error)
{
    HANDLE output = GetStdHandle(STD_ERROR_HANDLE);
    wchar_t detail[1024];
    DWORD written;
    int length;
    char utf8[4096];
    _snwprintf_s(detail, 1024, _TRUNCATE,
        L"CC Translate: %s (Windows error %lu).\n"
        L"Run the CC Translate installer to repair the Python runtime.\n",
        message, error);
    length = WideCharToMultiByte(CP_UTF8, 0, detail, -1,
        utf8, sizeof(utf8), NULL, NULL);
    if (output && output != INVALID_HANDLE_VALUE && length > 0) {
        WriteFile(output, utf8, (DWORD)(length - 1), &written, NULL);
    } else if (!(GetErrorMode() & (SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX))) {
        MessageBoxW(NULL, detail, L"CC Translate", MB_OK | MB_ICONERROR);
    }
    return 1;
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, LPWSTR command, int show)
{
    wchar_t path[32768];
    wchar_t *config = NULL, *python, *library, *base_python, *end;
    char *bytes = NULL;
    HANDLE file;
    LARGE_INTEGER size;
    DWORD count, length;
    int chars, argc, result;
    wchar_t **argv;
    HMODULE module;
    PythonMain python_main;
    (void)instance;
    (void)previous;
    (void)command;
    (void)show;

    length = GetModuleFileNameW(NULL, path, 32768);
    if (!length || length + 9 >= 32768)
        return fail(L"Cannot locate the launcher", GetLastError());
    wcscat_s(path, 32768, L".runtime");
    file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_DELETE,
        NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE)
        return fail(L"Cannot read the runtime configuration", GetLastError());
    if (!GetFileSizeEx(file, &size) || size.QuadPart <= 0 || size.QuadPart > 262144) {
        CloseHandle(file);
        return fail(L"Invalid runtime configuration size", ERROR_INVALID_DATA);
    }
    bytes = (char *)HeapAlloc(GetProcessHeap(), 0, (SIZE_T)size.QuadPart);
    if (!bytes) {
        CloseHandle(file);
        return fail(L"Cannot allocate runtime configuration", ERROR_NOT_ENOUGH_MEMORY);
    }
    if (!ReadFile(file, bytes, (DWORD)size.QuadPart, &count, NULL)
            || count != (DWORD)size.QuadPart) {
        DWORD error = GetLastError();
        CloseHandle(file);
        HeapFree(GetProcessHeap(), 0, bytes);
        return fail(L"Cannot read runtime configuration", error);
    }
    CloseHandle(file);
    chars = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, bytes, (int)count, NULL, 0);
    if (chars > 0)
        config = (wchar_t *)HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY,
            ((SIZE_T)chars + 1) * sizeof(wchar_t));
    if (!config || !MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS,
            bytes, (int)count, config, chars)) {
        HeapFree(GetProcessHeap(), 0, bytes);
        if (config) HeapFree(GetProcessHeap(), 0, config);
        return fail(L"Invalid UTF-8 runtime configuration", ERROR_INVALID_DATA);
    }
    HeapFree(GetProcessHeap(), 0, bytes);
    python = next_line(config);
    library = next_line(python);
    base_python = next_line(library);
    end = next_line(base_python);
    if (wcscmp(config, L"CC Translate Python host 1") || !python || !*python
            || !library || !*library || !base_python || !*base_python || !end || *end) {
        HeapFree(GetProcessHeap(), 0, config);
        return fail(L"Invalid runtime configuration", ERROR_INVALID_DATA);
    }
    count = GetFileAttributesW(python);
    if (count == INVALID_FILE_ATTRIBUTES || (count & FILE_ATTRIBUTE_DIRECTORY)) {
        HeapFree(GetProcessHeap(), 0, config);
        return fail(L"The configured Python interpreter is missing", ERROR_FILE_NOT_FOUND);
    }

    /* Load from the actual installation, never from PATH or the app-data directory. */
    module = LoadLibraryExW(library, NULL,
        LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
    if (!module) {
        DWORD error = GetLastError();
        HeapFree(GetProcessHeap(), 0, config);
        return fail(L"Cannot load the configured Python runtime", error);
    }
    #pragma warning(suppress: 4191)
    python_main = (PythonMain)GetProcAddress(module, "Py_Main");
    if (!python_main) {
        HeapFree(GetProcessHeap(), 0, config);
        return fail(L"The configured runtime has no Python entry point", ERROR_PROC_NOT_FOUND);
    }
    argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (!argv || argc < 1) {
        HeapFree(GetProcessHeap(), 0, config);
        return fail(L"Cannot read launcher arguments", GetLastError());
    }
    /*
     * Use CPython's Windows launcher protocol. It also fixes the executable
     * search directory and is consumed/cleared by Python during initialization.
     */
    if (!SetEnvironmentVariableW(L"__PYVENV_LAUNCHER__", python)) {
        DWORD error = GetLastError();
        LocalFree(argv);
        HeapFree(GetProcessHeap(), 0, config);
        return fail(L"Cannot select the Python environment", error);
    }
    argv[0] = base_python;
    result = python_main(argc, argv);
    LocalFree(argv);
    HeapFree(GetProcessHeap(), 0, config);
    /* Python extensions can retain DLL callbacks until process teardown. */
    return result;
}
