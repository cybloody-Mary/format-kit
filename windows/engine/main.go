package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"io"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"sync"
	"syscall"

	"kugo-music-converter/internal/config"
	"kugo-music-converter/internal/handler"
	"kugo-music-converter/internal/logger"
	"kugo-music-converter/internal/service"
)

type event struct {
	Type string `json:"type"`
	Data any    `json:"data"`
}

func main() {
	logger.SetOutput(io.Discard)
	os.Exit(run(os.Args[1:], os.Stdin, os.Stdout))
}

func run(args []string, input io.Reader, output io.Writer) int {
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	encoder := json.NewEncoder(output)
	var encoderMu sync.Mutex
	emit := func(kind string, data any) error {
		encoderMu.Lock()
		defer encoderMu.Unlock()
		err := encoder.Encode(event{Type: kind, Data: data})
		if err != nil {
			cancel()
		}
		return err
	}
	fail := func(code string, userMessage string) int {
		_ = emit("error", service.BatchFileError{
			Code: code, UserMessage: userMessage, Severity: "error",
		})
		return 1
	}

	flags := flag.NewFlagSet("qqmusic-decoder-engine", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	ffmpeg := flags.String("ffmpeg", "", "absolute path to FFmpeg")
	if err := flags.Parse(args); err != nil || flags.NArg() != 0 || !filepath.IsAbs(*ffmpeg) {
		return fail("ERR_RUNTIME_MISSING", "请指定 FFmpeg 的绝对路径。")
	}

	requests := bufio.NewScanner(input)
	requests.Buffer(make([]byte, 64*1024), 16*1024*1024)
	var request handler.LocalConversionRequest
	if !requests.Scan() || json.Unmarshal(requests.Bytes(), &request) != nil {
		return fail("ERR_INVALID_REQUEST", "转换请求无效。")
	}
	if !filepath.IsAbs(request.OutputDir) {
		return fail("ERR_OUTPUT_REQUIRED", "请选择有效的本机输出目录。")
	}
	for _, path := range request.Paths {
		if !filepath.IsAbs(path) || !isQQAudio(path) {
			return fail("ERR_UNSUPPORTED_FORMAT", "请选择受支持的 QQ 音乐文件。")
		}
	}
	request.DBPath = ""

	// A caller that submits a single request and closes stdin can still await the batch.
	go func() {
		for requests.Scan() {
			var command struct {
				Cancel bool `json:"cancel"`
			}
			if json.Unmarshal(requests.Bytes(), &command) == nil && command.Cancel {
				cancel()
				return
			}
		}
	}()

	cfg := config.DefaultConfig()
	cfg.FFmpegBin = *ffmpeg
	cfg.DefaultOutput = os.TempDir()
	converter := handler.NewDesktopConvertHandler(cfg, "0.6.1")
	summary, err := converter.ConvertLocalPaths(ctx, request, func(_ string, payload any) {
		switch value := payload.(type) {
		case service.BatchProgressEvent:
			_ = emit("progress", value)
		case service.BatchFileDoneEvent:
			_ = emit("file", withoutErrorDetail(value))
		}
	})
	if err != nil {
		var publicError *handler.AppError
		if errors.As(err, &publicError) {
			_ = emit("error", service.BatchFileError{
				Code: publicError.Code, UserMessage: publicError.UserMessage,
				Suggestion: publicError.Suggestion, Severity: publicError.Severity,
			})
			return 1
		}
		return fail("ERR_ENGINE", "转换引擎未能完成请求。")
	}
	for i, result := range summary.Results {
		summary.Results[i] = withoutErrorDetail(result)
	}
	if emit("complete", summary) != nil {
		return 1
	}
	return 0
}

func isQQAudio(path string) bool {
	switch strings.ToLower(filepath.Ext(path)) {
	case ".mflac", ".mgg", ".qmc0", ".qmc2", ".qmc3", ".qmc4", ".qmc6", ".qmc8", ".qmcflac", ".qmcogg", ".tkm":
		return true
	default:
		return false
	}
}

func withoutErrorDetail(result service.BatchFileDoneEvent) service.BatchFileDoneEvent {
	if result.Error != nil {
		publicError := *result.Error
		publicError.Detail = ""
		result.Error = &publicError
	}
	return result
}
