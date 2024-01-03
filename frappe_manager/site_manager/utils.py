import time

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
