from time import sleep
from rich.spinner import Spinner, SPINNERS
from rich.panel import Panel
from rich.console import Console
from rich.live import Live
from rich.columns import Columns


class spin:
    def __init__(self,text:str):
        self.console = Console()
        #self.panel = Panel(self.spinner,title='wow',title_align='left')
        self.previous_head = text
        self.current_head = text
        self.spinner = Spinner(text=self.current_head,name='squareCorners',speed=1)
        self.live = Live(self.spinner,console=self.console)
        self.live.start()

    def update_head(self,text:str):
        self.previous_head = self.current_head
        self.current_head = text
        self.live.console.print(self.previous_head)
        self.spinner.update(text=self.current_head)

    def change_head(self,text:str):
        self.previous_head = self.current_head
        self.current_head = text
        self.spinner.update(text=self.current_head)

    def stop(self):
        self.live.stop()

# if __name__ == '__main__':
#     s = spin('Working')
#     sleep(5)
#     s.change_head('sleep 3')
#     sleep(3)
#     s.update_head('sleep 9')
#     sleep(4)
#     s.update_head('exit')
#     sleep(4)
#     s.stop()


running_coloumn = Columns(['sdfdsf','wow','no'],align="center")
panel =  Panel(running_coloumn)
console = Console()
console.print(panel)
