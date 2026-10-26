import sys
import shutil

class ReplSkin:
    """Branded terminal skin for CLI-Anything interactive REPLs."""
    
    # ANSI color constants
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"
    GRAY = "\033[90m"

    def __init__(self, app_name="sweethome3d", version="1.2.0"):
        self.app_name = app_name
        self.version = version

    def print_banner(self):
        width = min(shutil.get_terminal_size((80, 20)).columns, 80)
        border = "=" * width
        print(f"{self.CYAN}{self.BOLD}{border}{self.RESET}")
        print(f"{self.CYAN}{self.BOLD}  CLI-Anything: Sweet Home 3D Agent-Native Terminal Harness v{self.version}{self.RESET}")
        print(f"{self.GRAY}  Making Architectural Floor Planning Autonomous & Agent-Native{self.RESET}")
        print(f"{self.GRAY}  Type 'help' for commands, 'exit' or Ctrl-C to quit.{self.RESET}")
        print(f"{self.CYAN}{self.BOLD}{border}{self.RESET}\n")

    def print_goodbye(self):
        print(f"\n{self.CYAN}Exiting CLI-Anything Sweet Home 3D. Goodbye!{self.RESET}")

    def success(self, msg):
        print(f"{self.GREEN}{self.BOLD}✓ {msg}{self.RESET}")

    def error(self, msg):
        print(f"{self.RED}{self.BOLD}✗ {msg}{self.RESET}", file=sys.stderr)

    def warning(self, msg):
        print(f"{self.YELLOW}{self.BOLD}⚠ {msg}{self.RESET}")

    def info(self, msg):
        print(f"{self.BLUE}{self.BOLD}● {msg}{self.RESET}")

    def status(self, key, value):
        print(f"{self.CYAN}{key:<20}: {self.RESET}{value}")

    def table(self, headers, rows):
        if not rows:
            print(f"{self.GRAY}(None){self.RESET}")
            return
        
        # Calculate column widths
        col_widths = [len(str(h)) for h in headers]
        for row in rows:
            for i, val in enumerate(row):
                if i < len(col_widths):
                    col_widths[i] = max(col_widths[i], len(str(val)))
        
        # Print header
        header_str = " | ".join(f"{str(h):<{col_widths[i]}}" for i, h in enumerate(headers))
        separator = "-+-".join("-" * col_widths[i] for i in range(len(headers)))
        print(f"{self.BOLD}{header_str}{self.RESET}")
        print(f"{self.GRAY}{separator}{self.RESET}")
        
        # Print rows
        for row in rows:
            row_str = " | ".join(f"{str(val):<{col_widths[i]}}" for i, val in enumerate(row))
            print(row_str)
