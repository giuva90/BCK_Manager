# BCK Manager - Windows Task Scheduler Examples

Use the Windows Task Scheduler to automate backup runs.

## Using the command line (schtasks)

```powershell
# Run all backup jobs every day at 02:00 AM
schtasks /create /tn "BCK Manager - Daily Backup" /tr "bck-manager --run-all" /sc daily /st 02:00 /ru SYSTEM /rl HIGHEST

# Run a specific job every day at 03:00 AM
schtasks /create /tn "BCK Manager - DB Dumps" /tr "bck-manager --run-job db-dumps" /sc daily /st 03:00 /ru SYSTEM /rl HIGHEST

# Run a specific job every Sunday at 04:00 AM
schtasks /create /tn "BCK Manager - Weekly Configs" /tr "bck-manager --run-job docker-compose-configs" /sc weekly /d SUN /st 04:00 /ru SYSTEM /rl HIGHEST
```

## Using the Task Scheduler GUI

1. Open **Task Scheduler** (`taskschd.msc`)
2. Click **Create Task** (not "Create Basic Task")
3. **General** tab:
   - Name: `BCK Manager - Daily Backup`
   - Select "Run whether user is logged on or not"
   - Check "Run with highest privileges"
4. **Triggers** tab:
   - New → Daily, Start at 02:00 AM
5. **Actions** tab:
   - New → Start a program
   - Program: `bck-manager`
   - Arguments: `--run-all`
6. **Settings** tab:
   - Check "Allow task to be run on demand"
   - Check "If the task fails, restart every: 5 minutes"
   - Set "Attempt to restart up to: 3 times"

## Viewing and managing tasks

```powershell
# List all BCK Manager tasks
schtasks /query /tn "BCK Manager*"

# Delete a task
schtasks /delete /tn "BCK Manager - Daily Backup" /f

# Run a task manually
schtasks /run /tn "BCK Manager - Daily Backup"
```
