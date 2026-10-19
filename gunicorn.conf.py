bind = "0.0.0.0:5004"
worker_class = "gthread"
# One worker: it runs the background fetch loop, and two would fetch twice.
workers = 1
threads = 4
timeout = 120
accesslog = "-"
errorlog = "-"
proc_name = "situation"
