// Format Kit starts its browser interface with an adjacent Python runtime.
package main

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"unsafe"
)

const windowTitle = "Format Kit"

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		showError(err.Error())
		os.Exit(1)
	}
}

func run() error {
	executable, err := os.Executable()
	if err != nil {
		return fmt.Errorf("无法找到启动器所在目录：%w", err)
	}
	root := filepath.Dir(executable)
	python := filepath.Join(root, "runtime", "python.exe")
	server := filepath.Join(root, "server.py")
	for _, path := range []string{python, server} {
		info, err := os.Stat(path)
		if err != nil || !info.Mode().IsRegular() {
			return fmt.Errorf("无法打开 %s。请解压整个软件包后再运行。", path)
		}
	}

	arguments := append([]string{"-X", "utf8", server}, os.Args[1:]...)
	command := exec.Command(python, arguments...)
	command.Dir = root
	const createNoWindow = 0x08000000
	command.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: createNoWindow}
	if _, err := command.Output(); err != nil {
		var exit *exec.ExitError
		if errors.As(err, &exit) {
			details := strings.TrimSpace(string(exit.Stderr))
			if details != "" {
				return fmt.Errorf("本地服务出错。\n\n%s", details)
			}
		}
		return fmt.Errorf("本地服务启动或运行失败。\n\n%w", err)
	}
	return nil
}

func showError(message string) {
	title, err := syscall.UTF16PtrFromString(windowTitle)
	if err != nil {
		return
	}
	body, err := syscall.UTF16PtrFromString(message)
	if err != nil {
		return
	}
	messageBox := syscall.NewLazyDLL("user32.dll").NewProc("MessageBoxW")
	const errorIcon = 0x10
	messageBox.Call(0, uintptr(unsafe.Pointer(body)), uintptr(unsafe.Pointer(title)), errorIcon)
}
