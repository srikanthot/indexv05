Hi Prakash,

As discussed in yesterday’s call, I checked the Application Insights setup for the web app. The Application Insights resource being used is `psegtmappiuatv01`, and telemetry is currently flowing for the application.

Below are the key performance metrics that we can monitor from Application Insights:

1. Server Requests / Request Count
   This shows the number of requests coming into the application during the selected performance test window.

2. Server Response Time / Request Duration
   This helps us track the average response time of the application and identify any slow API endpoints. From the Performance blade, we can drill down by operation name, such as `/chat/stream`, `/health`, or `/conversations`.

3. Failed Requests
   This shows the failed request count and failed operations. It helps us track application failures during the test window.

4. Response Codes
   The Failures blade shows HTTP response codes such as 404, 401, and any 5xx server-side errors if they occur during testing. This helps us classify whether the failure is related to routing, authentication, client-side errors, or server-side issues.

5. Exceptions
   Application Insights captures exception types and related failure details. This helps us identify application-level errors and supports debugging/root cause analysis.

6. Dependency Calls / Dependency Duration
   Live Metrics shows dependency call rate, dependency duration, and dependency failure rate. This helps us understand whether slowness is coming from backend dependencies or downstream service calls.

For real-time monitoring during performance execution, we can use the Live Metrics blade. It shows request rate, request duration, failure rate, dependency call rate, exception rate, CPU, memory, and server health in near real time.

For issue identification and root cause analysis, if we observe slowness, for example if a chat/PDF-related request takes 40–50 seconds, we will first check the Performance blade to identify the slow operation. Then we will drill into the request samples and review related dependency calls, exceptions, failed requests, and traces. If we see 5xx errors, we will review the Failures blade to check the failed operation, response code, exception type, and related telemetry. Based on that, we can narrow down whether the issue is coming from the application layer, dependency layer, infrastructure/resource usage, or configuration/authentication side.

One note: Availability monitoring is available in Application Insights, but I do not see an active availability test configured currently. Also, diagnostic export settings are not configured at this point, so if telemetry needs to be exported to Log Analytics, Storage, or Event Hub for long-term retention/reporting, that may need separate configuration.

For the Load Balancer point, I do not see a dedicated Azure Load Balancer configured from our side. Since the application is hosted on Azure App Service / Premium V3 compute, the platform-level traffic distribution is managed by Azure App Service. We can additionally confirm the current instance count and scale-out/autoscale configuration from the App Service Plan if needed.

