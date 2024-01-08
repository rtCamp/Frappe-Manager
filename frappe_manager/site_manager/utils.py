import time
import platform
import subprocess
import os

def is_port_in_use(port):
    """
    Check if port is in use or not.

    :param port: The port which will be checked if it's in use or not.
    :return: Bool In use then True and False when not in use.
    """
    import psutil
    for conn in psutil.net_connections():
        if conn.laddr.port == port and conn.status == 'LISTEN':
            return True
    return False

def check_ports(ports):
        """
        This function checks if the ports is in use.
        :param ports: list of ports to be checked
        returns: list of binded port(can be empty)
        """

        # TODO handle if ports are open using docker

        current_system = platform.system()

        already_binded = []

        for port in ports:
            if current_system == 'Darwin':

                # Mac Os
                # check port using lsof command
                cmd = f"lsof -iTCP:{port} -sTCP:LISTEN -P -n"
                try:
                    output = subprocess.run(cmd,check=True,shell=True,capture_output=True)
                    if output.returncode == 0:
                        already_binded.append(port)
                except subprocess.CalledProcessError as e:
                    pass
            else:
                # Linux or any other machines
                if is_port_in_use(port):
                    already_binded.append(port)

        return already_binded

def log_file(file, refresh_time:float = 0.1, follow:bool =False):
    '''
    Generator function that yields new lines in a file
    '''
    file.seek(0)

    # start infinite loop
    while True:
        # read last line of file
        line = file.readline()
        if not line:
            if not follow:
                break
            # sleep if file hasn't been updated
            time.sleep(refresh_time)
            continue
        line = line.strip('\n')
        yield line

def  get_container_name_prefix(site_name):
        return site_name.replace('.','')
