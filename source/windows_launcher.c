/* Start the portable Windows UI from the launcher directory, including after a move. */

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#define PATH_CAPACITY 32768
static WCHAR directory[PATH_CAPACITY];
static WCHAR interpreter[PATH_CAPACITY];
static WCHAR command_line[PATH_CAPACITY];
static STARTUPINFOW startup;
static PROCESS_INFORMATION child;

static DWORD append(WCHAR *buffer, DWORD end, const WCHAR *text)
{
    while (*text != L'\0')
        buffer[end++] = *text++;
    buffer[end] = L'\0';
    return end;
}

static int launch_failed(void)
{
    MessageBoxW(NULL, L"无法启动应用。请解压整个压缩包，再从解压后的文件夹打开 Start.exe。"
                      L"\n\n可以用同一文件夹里的 Start.cmd 查看错误信息。",
                L"QQ 音乐转码平台", MB_OK | MB_ICONERROR);
    return 1;
}

int WINAPI WinMain(HINSTANCE instance, HINSTANCE previous, LPSTR arguments, int show)
{
    (void)instance;
    (void)previous;
    (void)arguments;
    (void)show;

    static const WCHAR runtime_path[] = L"runtime\\python.exe";
    static const WCHAR script_arguments[] = L"\" -B win_server.py";
    DWORD length = GetModuleFileNameW(NULL, directory, ARRAYSIZE(directory));

    if (length == 0 || length >= ARRAYSIZE(directory))
        return launch_failed();

    while (length > 0 && directory[length - 1] != L'\\')
        --length;
    if (length == 0 || 1 + length + ARRAYSIZE(runtime_path) + ARRAYSIZE(script_arguments)
        > ARRAYSIZE(command_line))
        return launch_failed();
    directory[length] = L'\0';

    DWORD directory_end = append(interpreter, 0, directory);
    append(interpreter, directory_end, runtime_path);

    DWORD end = append(command_line, 0, L"\"");
    end = append(command_line, end, interpreter);
    append(command_line, end, script_arguments);

    startup.cb = sizeof(startup);
    if (!CreateProcessW(interpreter, command_line, NULL, NULL, FALSE, CREATE_NO_WINDOW,
                        NULL, directory, &startup, &child))
        return launch_failed();

    CloseHandle(child.hThread);
    CloseHandle(child.hProcess);
    return 0;
}
