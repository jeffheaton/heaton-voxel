// Which build of HeatonVoxel this is: the twin of heaton_voxel/version.py.
// Version is the package's version, the one HeatonVoxel.csproj and the Python
// package carry; tests/test_architecture.py holds them equal. The Build
// Library (.NET) workflow writes this file afresh before it builds, stamping
// BuildDate (UTC, ISO 8601) and Build (its run number). A checkout carries
// the values below: no date, build 0, meaning "not a release build".
namespace HeatonVoxel
{
    public static class HeatonVoxelVersion
    {
        public const string Version = "0.1.0";
        public const string BuildDate = "";
        public const int Build = 0;
    }
}
