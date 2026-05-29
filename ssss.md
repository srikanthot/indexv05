Hi Prakash,

Based on our current setup, we can use Azure Application Insights and Azure Monitor for performance monitoring and issue identification during the performance testing.

Below are the key metrics we can capture and use for monitoring:

1. Request Count / Throughput
    This helps us understand how many requests are coming into the application during the performance test window.
2. Response Time / Request Duration
    This helps us identify how long each API request is taking and whether any specific endpoint is responding slowly.
3. Failed Requests
    This helps us track failed requests, especially 4xx and 5xx errors. For 5xx errors, we can drill down further into the failure details.
4. Dependency Duration
    This helps us identify delays from downstream dependencies such as Azure AI Search, Azure OpenAI, Blob Storage, SharePoint, or any other external API calls, based on the telemetry available.
5. Exceptions
    This helps us capture application-level exceptions, error messages, and stack traces, which will be useful for debugging and root cause analysis.
6. Live Metrics / Resource Health
    During the actual performance test, we can use Live Metrics to monitor request rate, failure rate, exception rate, CPU, memory, and overall application health in near real time.

For the monitoring strategy, during performance execution we will first monitor the overall request volume, response time, and failure rate in Application Insights. If we observe slowness, for example if a PDF-related request is taking 40–50 seconds, we will drill down into the request details and check the dependency timeline to identify whether the delay is coming from the application layer, Azure AI Search, OpenAI/LLM call, Blob/SharePoint, network, or any backend dependency. If we observe 5xx errors, we will review the failed requests, exception details, stack trace, and related logs to identify the failure point. We will also use Live Metrics during the test window to observe the application behavior in real time. Based on the issue pattern, we can classify whether the bottleneck is application-side, infrastructure-side, or dependency-side. If any required RAG-level metric is not automatically captured, we can add custom logging or telemetry for more detailed tracking. This approach will help us identify the issue, narrow down the root cause, and take corrective action to avoid recurrence.

For the load balancer point, no dedicated Azure Load Balancer has been identified from our side at this point. Since the application is hosted on Azure App Service / Premium V3 compute, the platform-level traffic distribution is managed by Azure App Service. We can confirm the current instance count and scale-out/autoscale configuration from the App Service Plan and update this section accordingly.
