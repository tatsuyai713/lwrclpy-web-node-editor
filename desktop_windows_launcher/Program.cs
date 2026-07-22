using System.Diagnostics;
using System.Net;
using System.Net.Http;
using System.Net.Sockets;
using System.Text;
using Microsoft.Web.WebView2.WinForms;

namespace LwrclpyWebNodeEditor.Launcher;

internal static class Program
{
    private const string AppTitle = "lwrclpy Web Node Editor";

    [STAThread]
    private static int Main(string[] args)
    {
        if (args.Length > 0 && args[0] == "--desktop-import-check")
        {
            _ = typeof(WebView2);
            return File.Exists(BackendPath()) ? 0 : 2;
        }

        ApplicationConfiguration.Initialize();
        Application.Run(new AppForm());
        return 0;
    }

    private static string BackendPath()
    {
        return Path.Combine(AppContext.BaseDirectory, "lwrclpy-web-node-editor-backend.exe");
    }

    private static int FindFreePort()
    {
        using var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        return ((IPEndPoint)listener.LocalEndpoint).Port;
    }

    private sealed class AppForm : Form
    {
        private readonly Process _backend;
        private readonly string _appUrl;
        private readonly WebView2 _webView;

        public AppForm()
        {
            Text = AppTitle;
            Width = 1400;
            Height = 900;
            MinimumSize = new Size(800, 600);

            var backendPath = BackendPath();
            if (!File.Exists(backendPath))
            {
                throw new FileNotFoundException("Backend executable was not found.", backendPath);
            }

            var port = FindFreePort();
            _appUrl = $"http://127.0.0.1:{port}";
            _backend = new Process
            {
                StartInfo = new ProcessStartInfo
                {
                    FileName = backendPath,
                    Arguments = $"--server --host 127.0.0.1 --port {port}",
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    WorkingDirectory = AppContext.BaseDirectory,
                },
                EnableRaisingEvents = true,
            };
            _backend.Start();

            _webView = new WebView2 { Dock = DockStyle.Fill };
            Controls.Add(_webView);
            Load += OnLoad;
            FormClosing += OnFormClosing;
        }

        private async void OnLoad(object? sender, EventArgs e)
        {
            try
            {
                await WaitForServerAsync(_appUrl + "/api/health", TimeSpan.FromSeconds(15));
                await _webView.EnsureCoreWebView2Async();
                _webView.Source = new Uri(_appUrl);
            }
            catch (Exception ex)
            {
                MessageBox.Show(this, ex.Message, AppTitle, MessageBoxButtons.OK, MessageBoxIcon.Error);
                Close();
            }
        }

        private async void OnFormClosing(object? sender, FormClosingEventArgs e)
        {
            try
            {
                using var client = new HttpClient { Timeout = TimeSpan.FromSeconds(1) };
                using var content = new StringContent("{\"force\":true}", Encoding.UTF8, "application/json");
                await client.PostAsync(_appUrl + "/api/stop", content);
            }
            catch
            {
            }

            try
            {
                if (!_backend.HasExited)
                {
                    _backend.Kill(entireProcessTree: true);
                    _backend.WaitForExit(2000);
                }
            }
            catch
            {
            }
        }

        private static async Task WaitForServerAsync(string healthUrl, TimeSpan timeout)
        {
            using var client = new HttpClient { Timeout = TimeSpan.FromSeconds(1.5) };
            var deadline = DateTime.UtcNow + timeout;
            while (DateTime.UtcNow < deadline)
            {
                try
                {
                    using var response = await client.GetAsync(healthUrl);
                    if (response.IsSuccessStatusCode)
                    {
                        return;
                    }
                }
                catch
                {
                }

                await Task.Delay(100);
            }

            throw new TimeoutException("Desktop app server did not become ready in time.");
        }
    }
}
