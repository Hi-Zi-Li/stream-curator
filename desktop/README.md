# Desktop

This is the minimal Electron shell for the push homepage.

What it does:

- starts the frontend renderer
- exposes push IPC through `preload.js`
- launches the Python worker in the background
- reads `client push`
- triggers `client push --refresh`
- opens original URLs externally

Run:

```powershell
cd desktop
npm start
```
