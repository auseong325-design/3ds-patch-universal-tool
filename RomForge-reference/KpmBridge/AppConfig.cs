namespace RomForge.Core.Services.N3DS;

// The standalone bridge has no WPF configuration. Match RomForge's default.
internal sealed class AppConfig
{
    public static AppConfig Instance { get; } = new();
    public AzaharConfig Azahar { get; } = new();
    public sealed class AzaharConfig { public int CompressLevel => 18; }
}
