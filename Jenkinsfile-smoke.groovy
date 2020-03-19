/*
 * Requires: https://github.com/RedHatInsights/insights-pipeline-lib
 */

@Library("github.com/RedHatInsights/insights-pipeline-lib@v3") _


if (env.CHANGE_ID) {
    execSmokeTest (
        ocDeployerBuilderPath: "marketplace/marketplace",
        ocDeployerComponentPath: "marketplace",
        ocDeployerServiceSets: "ingress,platform-mq,marketplace",
        iqePlugins: ["iqe-marketplace-plugin"],
        pytestMarker: "smoke",
        // local settings file
        configFileCredentialsId: "marketplace_smoke_yaml",
    )
}
