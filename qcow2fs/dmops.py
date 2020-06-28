import argparse
from contextlib import ContextDecorator
from datetime import datetime
import os
import shutil
import sys
import subprocess
from subprocess import Popen, PIPE
import threading
import traceback

try:
    from queue import Queue, Empty
except ImportError:
    from Queue import Queue, Empty  # python 2.x


def execute(args):
    try:
        cp = subprocess.run(args, stdin=None, input=None,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, shell=False, cwd=None,
                            timeout=None, check=True, encoding=None,
                            errors=None, env=None,
                            universal_newlines=None)
        if cp.stderr:
            stderr = str(cp.stderr, 'utf-8').split('\n')[1]
            if stderr:
                print(stderr)
                raise Exception(stderr)
        return str(cp.stdout, 'utf-8')
    except subprocess.CalledProcessError as ex:
        # Log this message
        print(str(ex.stderr, 'utf-8'))
        traceback.print_stack()
        raise
    except Exception as ex:
        traceback.print_stack()
        raise


prog = "../build/qcow2fs/qcow2fs"

class Qcow2FSSession(ContextDecorator):
    def __init__(self, qcow2file):
        self.qcow2file = qcow2file

    def __enter__(self):
        env = os.environ.copy()
        cmd = "%s %s" % (prog, self.qcow2file)
        self.process = Popen(cmd.split(), stdin=PIPE, stdout=PIPE,
                             stderr=subprocess.STDOUT, shell=False, env=env)

        self.queue = Queue()
        def writeall(p, q):
            for line in iter(p.stdout.readline, b''):
                q.put(line.decode())
            p.stdout.close()

        writer = threading.Thread(target=writeall, args=(self.process, self.queue))
        writer.start()

        command_output = ""
        try:
            while True:
                line = self.queue.get(timeout=.2) # or q.get(timeout=.1)
                command_output += line
        except:
            pass
        return self

    def __exit__(self, *exc):
        self.quit()
        return False

    def send_cmd(self, message):
        try:
            message += '\n\r'
            self.process.stdin.write(message.encode())
            self.process.stdin.flush()

            command_output = ""
            try:
                while True:
                    line = self.queue.get(timeout=.2) # or q.get(timeout=.1)
                    if not 'qcow2fs:' in line:
                        command_output += line
            except Empty:
                return command_output
        except BrokenPipeError:
            return None

    def quit(self):
        self.send_cmd("quit")

    def get_blocks(self, pathname):
        blocks_str = self.send_cmd("blocks %s" %pathname)

        return blocks_str.split()

    def zap_blocks(self, blocks):
        for b in blocks:
            self.send_cmd("zap %s" % (str(b)))

    def backup_file(self, src, dest):
        pass

    def delete_file(self, pathname):
        return self.send_cmd("rm %s" % (str(pathname)))

    def rmtree(self, pathname):
        for path, dirs, files in self.walk(pathname):
           for f in files:
               self.delete_file(os.path.join(path, f))

           for d in dirs:
               self.rmtree(os.path.join(path, d))

        return self.send_cmd("rmdir %s" % (str(pathname)))

    def mkdir(self, pathname):
        return self.send_cmd("mkdir %s" % (str(pathname)))

    def stat_file(self, pathname):
        stat_str = self.send_cmd("stat %s" % (str(pathname)))
        stat = {}

        for s in stat_str.split("\n"):
            if "ctime" in s:
                 stat['st_ctime'] = datetime.strptime(s.split('--')[1].strip(), "%c")
            elif "atime" in s:
                 stat['st_atime'] = datetime.strptime(s.split('--')[1].strip(), "%c")
            elif "mtime" in s:
                 stat['st_mtime'] = datetime.strptime(s.split('--')[1].strip(), "%c")
            elif "crtime" in s:
                 stat['st_crtime'] = datetime.strptime(s.split('--')[1].strip(), "%c")

        stat['type'] = stat_str.split("\n")[0].split('Type:')[1].split('Mode:')[0].strip()
        stat['mode'] = stat_str.split("\n")[0].split('Mode:')[1].split('Flags:')[0].strip()

        stat['user'] = int(stat_str.split("\n")[2].split('User:')[1].split('Group:')[0].strip())
        stat['group'] = int(stat_str.split("\n")[2].split('Group:')[1].split('Size:')[0].strip())
        stat['size'] = int(stat_str.split("\n")[2].split('Size:')[1].strip())
        return stat

    def exists(self, pathname):
        try:
            self.stat_file(pathname)
            return True
        except:
            return False

    def isfile(self, pathname):
        try:
            stat = self.stat_file(pathname)
            return stat['type'] == 'regular'
        except:
            return False

    def makedirs(self, pathname):
  
        d, f = os.path.split(pathname)
        if d != '/':
             self.makedirs(d)
    
        if not self.exists(pathname):
            self.mkdir(self, pathname)

    def walk(self, pathname):
        ls_str = self.send_cmd("ls -l %s" % (str(pathname)))

        dirs = []
        files = []
        for l in ls_str.split("\n"):
            if not l:
                continue
            try:
                if l.split()[8] in [".", ".."]:
                    continue
                if l.split()[1].startswith("40"):
                    dirs.append(l.split()[8])
                else:
                    files.append(l.split()[8])
            except IndexError as ex:
                print(l, len(l.split()))
        yield pathname, dirs, files

        for d in dirs:
           yield from self.walk(os.path.join(pathname, d))

    def rdump(self, pv_mnt_dir, qcow2_dir='/'):
        return self.send_cmd("rdump %s %s" % (qcow2_dir, pv_mnt_dir))


def full_backup(pv_mnt, qcow2path):
    df_cmd = "df -h %s" % pv_mnt
    args = df_cmd.split()
    df_str = execute(args)

    size = df_str.split("\n")[1].split()[1]

    virt_cmd  = "virt-make-fs -F qcow2 -s %s %s %s" % (size, pv_mnt, qcow2path)
    args = virt_cmd.split()

    return execute(args)


def incr_backup(pv_mnt, qcow2path):
    # copy modified or new files from prod pv to backup
    with Qcow2FSSession(qcow2path) as session:
        for path, dirs, files in os.walk(pv_mnt):
            print(path)
            for f in files:
                try:
                    src = os.path.join(path, f)
                    dst = os.path.join(path, f)
                    dst = dst.replace(pv_mnt, "/", 1)
                    pv_st = os.stat(src, follow_symlinks=False)

                    if not session.exists(os.path.split(dst)[0]):
                        # make sure we have the directory path for new src
                        session.makedirs(os.path.split(dst)[0])

                    if os.path.exists(dst):
                        qcow2_st = session.stat_file(dst)
                        if pv_st.st_mtime != qcow2_st['st_mtime']:
                            session.backup_file(src, dst) 
                except Exception as ex:
                    print(ex)

            # take care of emptry/new directories
            for d in dirs:
                try:
                    src = os.path.join(path, d)
                    dst = os.path.join(path, d)
                    dst = dst.replace(pv_mnt, "/", 1)
                    pv_st = os.stat(src, follow_symlinks=False)
                    if not session.exists(dst):
                        # make sure we have the directory path for new src
                        session.makedirs(dst)
                except Exception as ex:
                    print(ex)

        # remove any files that are deleted since last backup
        for path, dirs, files in session.walk("/"):
            print(path)
            for f in files:
                try:    
                    src = os.path.join(path, f)
                    dst = os.path.join(path, f)
                    dst = dst.replace("/", pv_mnt, 1)
                    if not os.path.exists(dst):
                        if session.isfile(src):
                            session.delete_file(src)
                except Exception as ex:
                    print(ex)

            # take care of emptry/removed directories
            for d in dirs:
                try:
                    src = os.path.join(path, d)
                    dst = os.path.join(path, d)
                    dst = dst.replace("/", pv_mnt, 1)
                    if not os.path.exists(dst):
                        # make sure we have the directory path for new src
                        session.rmtree(src)
                except Exception as ex:
                    print(ex)


def restore(pv_mnt, qcow2path):
    with Qcow2FSSession(qcow2path) as session:
         session.rdump(pv_mnt)


def compare(pv_mnt, qcow2path):
    with Qcow2FSSession(qcow2path) as session:
        for path, dirs, files in os.walk(pv_mnt):
            pass

        for path, dirs, files in session.walk("/"):
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(title="commands",
                                       dest="command",
                                       help='sub-command help')
    full = subparsers.add_parser('full', help='full backup')
    full.add_argument('pv_mnt', help='Folder where the PV is mounted')
    full.add_argument('qcow2image', help='Path to qcow2 image')

    incr = subparsers.add_parser('incr', help='incremental backup')
    incr.add_argument('pv_mnt', help='Folder where the PV is mounted')
    incr.add_argument('qcow2image', help='Path to qcow2 image')

    restore_cmd = subparsers.add_parser('restore', help='restore')
    restore_cmd.add_argument('pv_mnt', help='Folder where the PV is mounted')
    restore_cmd.add_argument('qcow2image', help='Path to qcow2 image')

    compare_cmd = subparsers.add_parser('compare', help='compare')
    compare_cmd.add_argument('pv_mnt', help='Folder where the PV is mounted')
    compare_cmd.add_argument('qcow2image', help='Path to qcow2 image')

    args = parser.parse_args()

    if len(sys.argv)==1:
        parser.print_help()
        # parser.print_usage() # for just the usage line
        parser.exit()
 
    pv_mnt = args.pv_mnt
    if not pv_mnt.endswith("/"):
        pv_mnt += "/"

    if args.command == "full":
        full_backup(pv_mnt, args.qcow2image)
    elif args.command == "incr":
        incr_backup(pv_mnt, args.qcow2image)
    elif args.command == "restore":
        restore(pv_mnt, args.qcow2image)
    elif args.command == "compare":
        compare(pv_mnt, args.qcow2image)
