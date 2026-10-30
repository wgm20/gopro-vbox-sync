// Reflection-only host for the installed VBOX Video Setup public scene importer.
// Build with the Windows C# compiler and run with .NET Windows Desktop 8.
// No Racelogic binaries or scene assets are included in this helper.
using System;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;

internal static class SceneReader
{
    private const long MaximumSceneBytes = 32L * 1024L * 1024L;
    private static string dependencyDirectory;

    [STAThread]
    private static int Main(string[] args)
    {
        if (args.Length != 3)
        {
            Console.Error.WriteLine("Usage: scene-reader <VBOX Video Setup directory> <scene.vvhsn> <empty output directory>");
            return 2;
        }

        try
        {
            dependencyDirectory = Path.GetFullPath(args[0]);
            string source = Path.GetFullPath(args[1]);
            string destination = Path.GetFullPath(args[2]);
            string library = Path.Combine(dependencyDirectory, "VBoxVideo.FileRoutines.dll");
            if (!Directory.Exists(dependencyDirectory) || !File.Exists(library))
                throw new IOException("The installed VBOX Video Setup scene importer was not found.");
            if (!File.Exists(source) || !String.Equals(Path.GetExtension(source), ".vvhsn", StringComparison.OrdinalIgnoreCase))
                throw new IOException("Choose an existing .VVHSN scene file.");
            if (new FileInfo(source).Length > MaximumSceneBytes)
                throw new IOException("The scene file exceeds the 32 MB limit.");
            if (!Directory.Exists(destination) || Directory.GetFileSystemEntries(destination).Length != 0)
                throw new IOException("The scene output directory must already exist and be empty.");
            if (IsWithin(source, destination) || IsWithin(destination, dependencyDirectory) || IsWithin(dependencyDirectory, destination))
                throw new IOException("Use a separate temporary output directory for the scene import.");

            byte[] originalHash = Hash(source);
            AppDomain.CurrentDomain.AssemblyResolve += ResolveInstalledDependency;
            Assembly libraryAssembly = Assembly.LoadFrom(library);
            Type product = libraryAssembly.GetType("VideoVBoxHD.Settings.ProductType", true);
            Type importer = libraryAssembly.GetType("VideoVBoxHD.Settings.FileRoutines+Load", true);
            MethodInfo unpack = importer.GetMethod("UnpackageSceneFile", BindingFlags.Public | BindingFlags.Static,
                null, new Type[] { typeof(string), product, typeof(string) }, null);
            if (!importer.IsVisible || !product.IsVisible || unpack == null
                || unpack.ReturnType != typeof(string) || !product.IsEnum)
                throw new MissingMethodException("This VBOX Video Setup version does not expose the supported public scene importer.");

            object hd2 = Enum.Parse(product, "HD2", false);
            string unpacked;
            TextWriter standardOutput = Console.Out;
            try
            {
                // Keep the helper protocol to one result line even if the
                // installed importer writes informational console messages.
                Console.SetOut(TextWriter.Null);
                unpacked = unpack.Invoke(null, new object[] { source, hd2, destination }) as string;
            }
            finally
            {
                Console.SetOut(standardOutput);
            }

            if (!EqualHashes(originalHash, Hash(source)))
                throw new IOException("The original scene changed during import; select it again.");
            if (String.IsNullOrWhiteSpace(unpacked))
                throw new IOException("VBOX Video Setup returned no imported scene directory.");
            unpacked = Path.GetFullPath(unpacked);
            if (!IsWithin(unpacked, destination) || !Directory.Exists(unpacked))
                throw new IOException("VBOX Video Setup returned a scene outside the temporary output directory.");
            if (!File.Exists(Path.Combine(unpacked, "SCENE.XML")) || !File.Exists(Path.Combine(unpacked, "VBOXHD.XML")))
                throw new IOException("The imported scene is missing SCENE.XML or VBOXHD.XML.");
            Console.WriteLine(unpacked);
            return 0;
        }
        catch (Exception error)
        {
            while (error is TargetInvocationException && error.InnerException != null)
                error = error.InnerException;
            string message = error.Message.Replace('\r', ' ').Replace('\n', ' ').Trim();
            if (message.Length > 1200)
                message = message.Substring(0, 1200);
            Console.Error.WriteLine("Scene reader: " + message);
            return 1;
        }
    }

    private static Assembly ResolveInstalledDependency(object sender, ResolveEventArgs args)
    {
        string name = new AssemblyName(args.Name).Name;
        if (String.IsNullOrEmpty(name) || name.IndexOfAny(new char[] { '/', '\\', ':' }) >= 0)
            return null;
        string path = Path.Combine(dependencyDirectory, name + ".dll");
        return File.Exists(path) ? Assembly.LoadFrom(path) : null;
    }

    private static bool IsWithin(string path, string directory)
    {
        string fullPath = Path.GetFullPath(path).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        string fullDirectory = Path.GetFullPath(directory).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        return String.Equals(fullPath, fullDirectory, StringComparison.OrdinalIgnoreCase)
            || fullPath.StartsWith(fullDirectory + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase);
    }

    private static byte[] Hash(string path)
    {
        using (SHA256 sha = SHA256.Create())
        using (FileStream file = File.OpenRead(path))
            return sha.ComputeHash(file);
    }

    private static bool EqualHashes(byte[] first, byte[] second)
    {
        if (first.Length != second.Length)
            return false;
        for (int index = 0; index < first.Length; ++index)
            if (first[index] != second[index])
                return false;
        return true;
    }
}
