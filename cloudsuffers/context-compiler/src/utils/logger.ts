export interface Logger {
  debug(message: string, metadata?: Readonly<Record<string, unknown>>): void;
  info(message: string, metadata?: Readonly<Record<string, unknown>>): void;
  warn(message: string, metadata?: Readonly<Record<string, unknown>>): void;
  error(message: string, metadata?: Readonly<Record<string, unknown>>): void;
}

export class JsonConsoleLogger implements Logger {
  constructor(private readonly component: string) {}

  private write(level: string, message: string, metadata: Readonly<Record<string, unknown>> = {}): void {
    const line = JSON.stringify({ timestamp: new Date().toISOString(), level, component: this.component, message, ...metadata });
    if (level === "error") console.error(line);
    else if (level === "warn") console.warn(line);
    else console.log(line);
  }

  debug(message: string, metadata?: Readonly<Record<string, unknown>>): void { this.write("debug", message, metadata); }
  info(message: string, metadata?: Readonly<Record<string, unknown>>): void { this.write("info", message, metadata); }
  warn(message: string, metadata?: Readonly<Record<string, unknown>>): void { this.write("warn", message, metadata); }
  error(message: string, metadata?: Readonly<Record<string, unknown>>): void { this.write("error", message, metadata); }
}
