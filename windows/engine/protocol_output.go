package logger

import "io"

// SetOutput redirects diagnostic logs when stdout carries protocol messages.
func SetOutput(w io.Writer) { stdLog.SetOutput(w) }
