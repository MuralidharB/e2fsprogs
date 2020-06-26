import argparse
from datetime import datetime
import os
import shutil
import subprocess
import sys
import traceback

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


def run_qcow2fs_command(command, qcow2path):
    args = "./qcow2fs -R".split()
    args.append(command)
    args.append(qcow2path)

    return execute(args)


def get_blocks(pathname, qcow2path):
    blocks_str = run_qcow2fs_command("blocks %s" %pathname, qcow2path)

    return blocks_str.split()
    pass


def zap_blocks(blocks, qcow2path):
    for b in blocks:
        run_qcow2fs_command("zap %s" % (str(b)), qcow2path)


def backup_file(src, dest, qcow2path):
    pass


def delete_file(pathname, qcow2path):
    return run_qcow2fs_command("rm %s" % (str(pathname)), qcow2path)


def mkdir(pathname, qcow2path):
    return run_qcow2fs_command("mkdir %s" % (str(pathname)), qcow2path)


def stat_file(pathname, qcow2path):
    stat_str = run_qcow2fs_command("stat %s" % (str(pathname)), qcow2path)
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


def exists(pathname, qcow2path):
    try:
        stat_file(pathname, qcow2path)
        return True
    except:
        return False


def makedirs(pathname, qcow2path):
  
    d, f = os.path.split(pathname)
    if d != '/':
         makedirs(d, qcow2path)
    
    if not exists(pathname, qcow2path):
        mkdir(pathname, qcow2path)


def walk(pathname, qcow2path):
    ls_str = run_qcow2fs_command("ls -l %s" % (str(pathname)), qcow2path)

    dirs = []
    files = []
    for l in ls_str.split("\n"):
        if not l:
            continue
        if l.split()[8] in [".", ".."]:
             continue
        if l.split()[1].startswith("40"):
             dirs.append(l.split()[8])
        else:
             files.append(l.split()[8])
    yield pathname, dirs, files

    for d in dirs:
        walk(os.path.join(pathname, d), qcow2path)


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
    for path, dirs, files in os.walk(pv_mnt):
        for f in files:
            try:
                src = os.path.join(path, f)
                dst = os.path.join(path, f)
                dst = dst.replace(pv_mnt, "/")
                pv_st = os.stat(src, follow_symlinks=False)

                if not exists(os.path.split(dst)[0], qcow2path):
                    # make sure we have the directory path for new src
                    makedirs(os.path.split(dst)[0], qcow2path)

                if os.path.exists(dst):
                    qcow2_st = stat_file(dst, qcow2path)
                    if pv_st.st_mtime != qcow2_st['st_mtime']:
                        backup_file(src, dst, qcow2path) 
                else:
                    backup_file(src, dst, qcow2path) 
            except:
                pass

        # take care of emptry/new directories
        for d in dirs:
            try:
                src = os.path.join(path, d)
                dst = os.path.join(path, d)
                dst = dst.replace(pv_mnt, "/")
                pv_st = os.stat(src, follow_symlinks=False)
                if not exists(dst, qcow2path):
                    # make sure we have the directory path for new src
                    makedirs(dst, qcow2path)
            except:
                pass

    # remove any files that are deleted since last backup
    for path, dirs, files in walk("/", qcow2path):
        for f in files:
            try:    
                src = os.path.join(path, f)
                dst = os.path.join(path, f)
                dst = dst.replace("/", pv_mnt)
                if not os.path.exists(dst):
                    if isfile(src):
                        delete_file(src)
            except:
                pass

        # take care of emptry/removed directories
        for d in dirs:
            try:
                src = os.path.join(path, d)
                dst = os.path.join(path, d)
                dst = dst.replace("/", pv_mnt)
                if not os.path.exists(dst):
                    # make sure we have the directory path for new src
                    rmtree(src)
            except:
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

    restore = subparsers.add_parser('restore', help='restore')
    restore.add_argument('pv_mnt', help='Folder where the PV is mounted')
    restore.add_argument('qcow2image', help='Path to qcow2 image')

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

        run_qcow2fs_command("ls", "/home/centos/xxx.qcow2")
        blocks = get_blocks("Makefile.am", "/home/centos/xxx.qcow2")
        print(stat_file("Makefile.am", "/home/centos/xxx.qcow2"))
        zap_blocks(blocks,  "/home/centos/xxx.qcow2")
        blocks = delete_file("Makefile.am", "/home/centos/xxx.qcow2")
