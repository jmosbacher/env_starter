#!/cvmfs/xenon.opensciencegrid.org/releases/nT/development/anaconda/envs/XENONnT_development/bin/python
import argparse
import os
import os.path as osp
import shutil
import subprocess
import stat
import sys
import time
from random import choices
from string import ascii_lowercase

# the path to this file
ENVSTARTER_PATH = osp.dirname(osp.abspath(__file__))
# where you want to store sbatch and log files
OUTPUT_DIR = osp.expanduser('~/straxlab')

def printflush(x):
    """Does print(x, flush=True), also in python 2.x"""
    print(x)
    sys.stdout.flush()


SPLASH_SCREEN = r"""
 __   __ ______  _   _   ____   _   _      _______ 
 \ \ / /|  ____|| \ | | / __ \ | \ | |    |__   __|
  \ V / | |__   |  \| || |  | ||  \| | _ __  | |   
   > <  |  __|  | . ` || |  | || . ` || '_ \ | |   
  / . \ | |____ | |\  || |__| || |\  || | | || |   
 /_/ \_\|______||_| \_| \____/ |_| \_||_| |_||_|   

                    The UChicago Analysis Center

"""

JOB_HEADER = """#!/bin/bash
#SBATCH --job-name=straxlab
#SBATCH --output={log_fn}
#SBATCH --error={log_fn}
#SBATCH --account=pi-lgrandi
#SBATCH --ntasks=1
#SBATCH --cpus-per-task={n_cpu}
#SBATCH --mem-per-cpu={mem_per_cpu}
#SBATCH --time={max_hours}:00:00
{extra_header}

export NUMEXPR_MAX_THREADS={n_cpu}
echo Starting jupyter job

"""

GPU_HEADER = """\
#SBATCH --partition=gpu2
#SBATCH --gres=gpu:1

module load cuda/10.1
"""

CPU_HEADER = """\
#SBATCH --qos {partition}
#SBATCH --partition {partition}
{reservation}
"""


# This is only if the user is NOT starting the singularity container
# (for singularity, starting jupyter is done in _xentenv_inner)
START_JUPYTER = """
JUP_PORT=$(( 15000 + (RANDOM %= 5000) ))
JUP_HOST=$(hostname -i)
echo $PYTHONPATH
jupyter {jupyter} --no-browser --port=$JUP_PORT --ip=$JUP_HOST --notebook-dir {notebook_dir} 2>&1
"""

SUCCESS_MESSAGE = """
All done! If you have linux, execute this command on your laptop:

ssh -fN -L {port}:{ip}:{port} {username}@dali-login2.rcc.uchicago.edu && sensible-browser http://localhost:{port}/{token}

If you have a mac, instead do:

ssh -fN -L {port}:{ip}:{port} {username}@dali-login2.rcc.uchicago.edu && open "http://localhost:{port}/{token}"

Happy strax analysis, {username}!
"""


def make_executable(path):
    """Make the file at path executable, see """
    mode = os.stat(path).st_mode
    mode |= (mode & 0o444) >> 2    # copy R bits to X
    os.chmod(path, mode)


def parse_arguments():
    parser = argparse.ArgumentParser(
        description='Start a strax jupyter notebook server on the dali batch queue')
    parser.add_argument('--partition',
                        default='xenon1t', type=str,
                        help="RCC/DALI partition to use. Try dali, broadwl, or xenon1t.")
    parser.add_argument('--bypass_reservation', action='store_true',
                        help="Do not use the notebook reservation (useful if it is full)")
    parser.add_argument('--node', help="Specify a node, if desired. By default no specification made")
    parser.add_argument('--timeout',
                        default=120, type=int,
                        help='Seconds to wait for the jupyter server to start')
    parser.add_argument('--cpu',
                        default=2, type=int,
                        help='Number of CPUs to request.')
    parser.add_argument('--ram',
                        default=8000, type=int,
                        help='MB of RAM to request')
    parser.add_argument('--gpu',
                        action='store_true', default=False,
                        help='Request to run on a GPU partition. Limits runtime to 2 hours.')
    parser.add_argument('--env',
                        default='singularity',
                        choices=['singularity', 'cvmfs'],
                        help='Environment to activate; defaults to "singularity" '
                             'to load XENONnT singularity container. '
                             'Passing "cvmfs" will use the conda environment installed in cvmfs, '
                             'using the --tag argument to determine which env exactly ')
    parser.add_argument('--tag',
                        default='development',
                        help='Tagged environment to load'
                             'See wiki page https://xe1t-wiki.lngs.infn.it/doku.php?id=xenon:xenonnt:analysis:environments'   # noqa
                             'Default: "development", or -- equivalently -- "latest"')
    parser.add_argument('--max_hours',
                        default=None, type=float,
                        help='Max number of hours before the job expires. Defaults to 8 h for normal jobs and 2 for GPUs.')  # noqa
    parser.add_argument('--force_new',
                        action='store_true', default=False,
                        help='Start a new job even if you already have an old one running')
    parser.add_argument('--jupyter',
                        choices=['lab', 'notebook'],
                        default='lab',
                        help='Use jupyter-lab or jupyter-notebook')
    parser.add_argument('--notebook_dir', default=os.environ['HOME'],
                        help='The working directory passed to jupyter')
    parser.add_argument('--copy_tutorials',
                        action='store_true',
                        help='Copy tutorials to ~/strax_tutorials (if it does not exist)')
    parser.add_argument('--local_cutax',
                        action='store_true',
                        help='Enable the usage of locally installed cutax')

    return parser.parse_args()


def main():
    args = parse_arguments()
    print_flush(SPLASH_SCREEN)

    # Dir for the sbatch and log files
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    if args.local_cutax:
        os.environ['INSTALL_CUTAX'] = '0'

    if args.copy_tutorials:
        dest = os.path.join(OUTPUT_DIR, 'strax_tutorials')
        if osp.exists(dest):
            print_flush("NOT copying tutorials, folder already exists")
        else:
            shutil.copytree(
                '/dali/lgrandi/strax/straxen/notebooks/tutorials',
                dest)

    if args.env == 'singularity':
        s_container = 'xenonnt-%s.simg' % args.tag
        batch_job = JOB_HEADER + \
                    "{env_starter}/start_notebook.sh " \
                    "{s_container} {jupyter} {nbook_dir}".format(env_starter=ENVSTARTER_PATH,
                                                                 s_container=s_container,
                                                                 jupyter=args.jupyter,
                                                                 nbook_dir=args.notebook_dir,
                                                                )
    elif args.env == 'cvmfs':
        batch_job = (JOB_HEADER
                     + "source /cvmfs/xenon.opensciencegrid.org/releases/nT/%s/setup.sh" % (args.tag)
                     + START_JUPYTER.format(jupyter=args.jupyter,
                                            notebook_dir=args.notebook_dir)
                     )
        print_flush("Using conda from cvmfs (%s) instead of singularity container." % (args.tag))

    url = None
    url_cache_fn = osp.join(
        os.environ['HOME'],
        '.last_jupyter_url')
    username = os.environ['USER']
    
    # Check if a job is already running
    q = subprocess.check_output(['squeue', '-u', username])
    jobs = [line for line in q.decode().splitlines() if 'straxlab' in line]
    job_ids = [int(job.split()[0]) for job in jobs]
    unique_id = '' if len(job_ids) == 0 else '_' + get_unique_id()

    if job_ids:
        print_flush("You still have running straxlab jobs with ids [%s]!" % ",".join([str(id) for id in job_ids]))

    for job_id in job_ids:
        if not args.force_new:
            print_flush("\tTrying to retrieve the URL for job %d from " % job_id + url_cache_fn)
            print_flush("\tIf it doesn't work, login and cancel your job "
                        "so we can start a new one.")
            with open(url_cache_fn) as f:
                try:
                    cached_job_id, cached_url = f.read().split()
                except Exception as e:
                    print_flush("\tProblem reading cache file! " + str(e))
                    print_flush("\tWell, we can still start a new job...")
                else:
                    if int(cached_job_id) == job_id:
                        url = cached_url
                    else:
                        print_flush("\t... Unfortunately the cache file refers "
                                    "to a different job, id %s" % cached_job_id)
            if url is not None:
                break

    else:
        print_flush("Submitting a new jupyter job")

        use_reservation = (
            (not args.force_new)
            and args.partition == 'xenon1t'
            and (not args.bypass_reservation)
            and args.cpu < 8
        )

        job_fn = os.path.join(OUTPUT_DIR, f'notebook{unique_id}.sbatch')
        if not args.force_new:
            log_fn = os.path.join(OUTPUT_DIR, 'notebook.log')
        else:
            log_fn = os.path.join(OUTPUT_DIR, f'notebook_forced{unique_id}.log')
        if os.path.exists(log_fn):
            os.remove(log_fn)
        with open(job_fn, mode='w') as f:
            extra_header = (GPU_HEADER if args.gpu
                            else CPU_HEADER.format(partition=args.partition,
                                                   reservation=('#SBATCH --reservation=xenon_notebook'
                                                                if use_reservation else '')))
            if args.node:
                extra_header += '\n#SBATCH --nodelist={node}'.format(node=args.node)
            if args.max_hours is None:
                max_hours = 2 if args.gpu else 8
            else:
                max_hours = int(args.max_hours)
            f.write(batch_job.format(
                log_fn=log_fn,
                max_hours=max_hours,
                extra_header=extra_header,
                n_cpu=args.cpu,
                mem_per_cpu=int(args.ram / args.cpu)))
        make_executable(job_fn)
        print_flush("\tSubmitting sbatch %s" % job_fn)
        result = subprocess.check_output(['sbatch', job_fn])
        print_flush("\tsbatch returned: %s" % result)
        job_id = int(result.decode().split()[-1])
        print_flush("\tYou have job id %d" % job_id)

        print_flush("Waiting for your job to start")
        print_flush("\tLooking for logfile %s" % log_fn)
        while not osp.exists(log_fn):
            print_flush("\tstill waiting...")
            time.sleep(2)

        print_flush("Job started. Logfile is displayed below; "
                    "we're looking for the jupyter URL.")
        lines_shown = 0
        slept = 0
        url = None
        while url is None and slept < args.timeout:
            with open(log_fn, mode='r') as f:
                content = f.readlines()
                for line_i, line in enumerate(content):
                    if line_i >= lines_shown:
                        print_flush('\t' + line.rstrip())
                        lines_shown += 1
                    if 'http' in line and not any([excluded in line for excluded in ['sylabs', 'github.com']]):
                        url = line.split()[-1]
                        break
                else:
                    time.sleep(2)
                    slept += 2
        if url is None:
            raise RuntimeError("Jupyter did not start inside your job!")

        print_flush("\nJupyter started succesfully")

        print_flush("\tDumping URL %s to cache file %s" % (url, url_cache_fn))
        with open(url_cache_fn, mode='w') as f:
            f.write(str(job_id) + ' ' + url + '\n')
        # The token is in the file, so we had better do...
        os.chmod(url_cache_fn, stat.S_IRWXU)

    print_flush("\tParsing URL %s" % url)
    ip, port = url.split('/')[2].split(':')
    if 'token' in url:
        token = url.split('?')[1].split('=')[1]
        token = '?token=' + token
    else:
        token = ''
        
    # Check if many jobs are running
    q = subprocess.check_output(['squeue', '-u', username])
    jobs = [line for line in q.decode().splitlines() if 'straxlab' in line]
    job_ids = [int(job.split()[0]) for job in jobs]

    if len(job_ids) > 1:
        print_flush("\nPlease consider stopping remaining straxlab jobs:")
        for job in jobs:
            print_flush("\t" + job)

    print_flush(SUCCESS_MESSAGE.format(ip=ip, port=port, token=token, username=username))
    
    
def print_flush(x):
    """Does print(x, flush=True), also in python 2.x"""
    print(x)
    sys.stdout.flush()


def get_unique_id():
    return ''.join(choices(ascii_lowercase, k=6))


def make_executable(path):
    """Make the file at path executable, see """
    mode = os.stat(path).st_mode
    mode |= (mode & 0o444) >> 2    # copy R bits to X
    os.chmod(path, mode)


if __name__ == '__main__':
    main()
